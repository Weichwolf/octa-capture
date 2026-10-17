// SPDX-License-Identifier: GPL-2.0-only
#include "PresetDevice.hpp"

#include "octa/Midi.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <fcntl.h>
#include <iostream>
#include <optional>
#include <span>
#include <string_view>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

extern "C" {
#include <signal.h>
}

namespace octa::cli {
namespace {

volatile sig_atomic_t interrupted = 0;

void interrupt(int /*signal*/) {
  interrupted = 1;
}

bool install_handlers() {
  interrupted = 0;
  struct sigaction action{};
  action.sa_handler = &interrupt;
  return sigemptyset(&action.sa_mask) == 0 && sigaction(SIGINT, &action, nullptr) == 0 &&
         sigaction(SIGTERM, &action, nullptr) == 0;
}

int failure(const MidiError &error) {
  std::cerr << error.operation << ": error " << error.code << '\n';
  return 1;
}

std::expected<void, MidiError> write_file(int descriptor, std::span<const std::uint8_t> bytes) {
  while (!bytes.empty()) {
    const auto count = write(descriptor, bytes.data(), bytes.size());
    if (count < 0 && errno == EINTR) { continue; }
    if (count <= 0 || static_cast<std::size_t>(count) > bytes.size()) {
      return std::unexpected(
          MidiError{.code = count < 0 ? -errno : -EIO, .operation = "write recovery snapshot"});
    }
    bytes = bytes.subspan(static_cast<std::size_t>(count));
  }
  while (fsync(descriptor) != 0) {
    if (errno != EINTR) {
      return std::unexpected(MidiError{.code = -errno, .operation = "sync recovery snapshot"});
    }
  }
  return {};
}

std::expected<void, MidiError> save_snapshot(const char *path, const Message &snapshot) {
  const auto frame = encode(snapshot.command, snapshot.address, snapshot.payload);
  if (!frame) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "encode recovery snapshot"});
  }
  constexpr auto permissions = static_cast<mode_t>(S_IRUSR | S_IWUSR);
  const int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, permissions);
  if (descriptor < 0) {
    return std::unexpected(MidiError{.code = -errno, .operation = "create recovery snapshot"});
  }
  auto saved = write_file(descriptor, *frame);
  const int closed = close(descriptor);
  if (closed < 0 && saved) {
    saved = std::unexpected(MidiError{.code = -errno, .operation = "close recovery snapshot"});
  }
  if (!saved && unlink(path) != 0) {
    std::cerr << "Could not remove incomplete recovery snapshot: " << path << '\n';
  }
  return saved;
}

struct ApplyState {
  ApplyOptions options;
  std::optional<Message> snapshot;
  bool write_attempted = false;
};

std::expected<Message, MidiError> capture_snapshot(MidiSession &session, ApplyState &state) {
  auto before = session.read_bulk_state();
  if (!before) { return before; }
  if (!validate_bulk_write(*before)) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "recovery snapshot bounds"});
  }
  const auto saved = save_snapshot(state.options.snapshot_path, *before);
  if (!saved) { return std::unexpected(saved.error()); }
  state.snapshot = *before;
  if (interrupted != 0) {
    return std::unexpected(MidiError{.code = -EINTR, .operation = "operation interrupted"});
  }
  return before;
}

std::expected<Message, MidiError> attempt_apply(const Preset &preset, ApplyState &state) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  const auto before = capture_snapshot(*session, state);
  if (!before) { return before; }
  state.write_attempted = true;
  return session->write_preset(preset, *before);
}

std::expected<Message, MidiError> attempt_memory(MemoryAction action, ApplyState &state) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  const auto before = capture_snapshot(*session, state);
  if (!before) { return before; }
  state.write_attempted = true;
  return session->operate_memory(action);
}

std::expected<Message, MidiError> recover(const Message &snapshot, std::string_view port) {
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  return session->write_bulk_state(snapshot);
}

int failed_change(const MidiError &error, const ApplyState &state, bool restore_runtime) {
  failure(error);
  if (state.write_attempted && state.snapshot.has_value()) {
    if (restore_runtime) {
      // The attempted operation has closed its connection before recovery opens another.
      const auto restored = recover(*state.snapshot, state.options.port);
      if (restored) {
        std::cerr << "Original raw state restored and verified.\n";
      } else {
        failure(restored.error());
        std::cerr << "Automatic recovery failed; raw snapshot: " << state.options.snapshot_path
                  << '\n';
      }
    } else {
      std::cerr << "Persistent storage status is unknown; raw snapshot: "
                << state.options.snapshot_path << '\n';
    }
  }
  constexpr int interrupted_status = 130;
  return interrupted != 0 ? interrupted_status : 1;
}

}

int apply_preset(const Preset &preset, ApplyOptions options) {
  if (!validate_preset(preset)) {
    std::cerr << "Preset contains values outside known control bounds.\n";
    return 1;
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "preset signal handlers"});
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  auto result = attempt_apply(preset, state);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "preset interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, true); }
  std::cout << "Preset applied; complete bulk readback verified. Recovery snapshot: "
            << options.snapshot_path << '\n'
            << std::flush;
  return std::cout.good() ? 0 : 1;
}

int operate_memory(MemoryAction action, ApplyOptions options) {
  if (action != MemoryAction::store && action != MemoryAction::load) {
    return failure({.code = -EINVAL, .operation = "memory action"});
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "memory signal handlers"});
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  auto result = attempt_memory(action, state);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "memory interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, action == MemoryAction::load); }
  std::cout << (action == MemoryAction::store ? "Store" : "Load")
            << " command sent; current bulk state read. Recovery snapshot: "
            << options.snapshot_path
            << "\nPersistent storage/load completion is not verified by the bulk reply.\n"
            << std::flush;
  return std::cout.good() ? 0 : 1;
}

int restore_snapshot(const Message &snapshot, std::string_view port) {
  if (!validate_bulk_write(snapshot)) {
    std::cerr << "Recovery snapshot is invalid or outside known control bounds.\n";
    return 1;
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "preset signal handlers"});
  }
  // Recovery is completed even if a graceful interruption arrives while it runs.
  const auto restored = recover(snapshot, port);
  if (!restored) { return failure(restored.error()); }
  std::cout << "Raw state restored; complete bulk readback verified.\n" << std::flush;
  return std::cout.good() ? 0 : 1;
}

}
