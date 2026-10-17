// SPDX-License-Identifier: GPL-2.0-only
#include "PresetDevice.hpp"

#include "octa/AutoSense.hpp"
#include "octa/DirectOut.hpp"
#include "octa/DspControl.hpp"
#include "octa/Initialization.hpp"
#include "octa/Midi.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
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
volatile sig_atomic_t finish_requested = 0;

void interrupt(int signal) {
  if (signal == SIGUSR1) {
    finish_requested = 1;
  } else {
    interrupted = 1;
  }
}

bool install_handlers(bool interactive = false) {
  interrupted = 0;
  finish_requested = 0;
  struct sigaction action{};
  action.sa_handler = &interrupt;
  if (sigemptyset(&action.sa_mask) != 0 || sigaction(SIGINT, &action, nullptr) != 0 ||
      sigaction(SIGTERM, &action, nullptr) != 0) {
    return false;
  }
  if (!interactive) { return true; }
  if (sigaction(SIGUSR1, &action, nullptr) != 0) { return false; }
  action.sa_handler = SIG_IGN;
  return sigaction(SIGPIPE, &action, nullptr) == 0;
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

std::expected<Message, MidiError> attempt_initialization(InitializationTarget target,
                                                         ApplyState &state) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  const auto before = capture_snapshot(*session, state);
  if (!before) { return before; }
  state.write_attempted = true;
  const auto result = session->initialize_settings(target);
  if (!result) { return result; }
  const auto matches = initialization_scope_matches(*before, *result, target);
  if (!matches || !*matches) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "initialization state scope"});
  }
  const auto repeated = session->read_bulk_state();
  if (!repeated) { return repeated; }
  if (*repeated != *result) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "initialization unstable state"});
  }
  return result;
}

std::expected<Message, MidiError>
recover(const Message &snapshot, std::string_view port, bool cancel_auto = false) {
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  if (cancel_auto) {
    const auto cancelled = session->stop_auto_sense(AutoSenseAction::cancel);
    if (!cancelled) { return std::unexpected(cancelled.error()); }
  }
  return session->write_bulk_state(snapshot);
}

int failed_change(const MidiError &error,
                  const ApplyState &state,
                  bool restore_runtime,
                  bool cancel_auto = false) {
  failure(error);
  if (!state.write_attempted && state.snapshot.has_value()) {
    std::cerr << "Original raw state unchanged; no device write attempted.\n";
  }
  if (state.write_attempted && state.snapshot.has_value()) {
    if (restore_runtime) {
      // The attempted operation has closed its connection before recovery opens another.
      const auto restored = recover(*state.snapshot, state.options.port, cancel_auto);
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

std::expected<Message, MidiError> attempt_routing(RoutingPreset preset, ApplyState &state) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  const auto before = capture_snapshot(*session, state);
  if (!before) { return before; }
  // The original patchbay is unavailable at rate index 3 (192 kHz).
  if (before->payload[2] >= 3) {
    return std::unexpected(MidiError{.code = -ENOTSUP, .operation = "routing sample-rate limit"});
  }
  constexpr std::array<std::uint8_t, 5> multi_monitor = {0, 1, 2, 3, 0};
  constexpr std::array<std::uint8_t, 5> playback = {0, 5, 6, 7, 8};
  const auto &routes = preset == RoutingPreset::multi_monitor ? multi_monitor : playback;
  auto expected = *before;
  constexpr std::size_t routing_offset = 5;
  for (std::size_t pair = 0; pair < routes.size(); ++pair) {
    if (interrupted != 0) {
      return std::unexpected(MidiError{.code = -EINTR, .operation = "routing interrupted"});
    }
    state.write_attempted = true;
    const auto reply = session->write_dsp(
        RouteChange{.pair = static_cast<std::uint8_t>(pair), .selection = routes[pair]});
    if (!reply) { return reply; }
    expected.payload[routing_offset + pair] = routes[pair];
    if (*reply != expected) {
      return std::unexpected(
          MidiError{.code = -EIO, .operation = "routing unrelated state change"});
    }
  }
  return expected;
}

using Clock = std::chrono::steady_clock;

std::expected<Message, MidiError> attempt_direct_out(ApplyState &state) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  auto current = capture_snapshot(*session, state);
  if (!current) { return current; }
  const auto target = direct_out_target(*current);
  if (!target) {
    return std::unexpected(
        MidiError{.code = target.error() == DirectOutError::rate ? -ENOTSUP : -EINVAL,
                  .operation = "Direct Out state/rate bounds"});
  }
  constexpr auto maximum_duration = std::chrono::minutes(1);
  const auto deadline = Clock::now() + maximum_duration;
  for (const auto &change : direct_out_changes()) {
    if (interrupted != 0) {
      return std::unexpected(MidiError{.code = -EINTR, .operation = "Direct Out interrupted"});
    }
    if (Clock::now() >= deadline) {
      return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "Direct Out deadline"});
    }
    state.write_attempted = true;
    current = session->write_dsp(change);
    if (!current) { return current; }
  }
  if (*current != *target) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "Direct Out complete readback"});
  }
  return current;
}

struct AutoSenseProgress {
  AutoSenseChannels active{};
  std::uint8_t selected = 0;
  bool started = false;
  bool completed = false;
  std::size_t notifications = 0;
  bool interactive = false;
};

std::expected<void, MidiError> update_auto_sense(AutoSenseProgress &progress,
                                                 const Message &message) {
  if (message.command != Command::data || message.address[0] != 0 || message.address[1] != 2 ||
      message.address[2] != 1 || (message.address[3] != 4 && message.address[3] != 5)) {
    return {};
  }
  const auto changed = apply_auto_sense_update(progress.active, message);
  if (!changed) {
    return std::unexpected(MidiError{.code = -EBADMSG, .operation = "Auto-Sens notification"});
  }
  ++progress.notifications;
  const bool was_started = progress.started;
  const bool active = std::ranges::any_of(progress.active, [](bool channel) { return channel; });
  for (std::size_t channel = 0; channel < progress.active.size(); ++channel) {
    if (progress.active[channel] && (progress.selected & (1U << channel)) != 0) {
      progress.started = true;
    }
  }
  progress.completed = progress.started && !active;
  if (progress.interactive && progress.started && !was_started) {
    std::cout << "{\"event\":\"auto-sense-started\"}\n" << std::flush;
    if (!std::cout.good()) {
      return std::unexpected(MidiError{.code = -EPIPE, .operation = "Auto-Sens progress output"});
    }
  }
  return {};
}

std::expected<void, MidiError> wait_auto_sense(MidiSession &session,
                                               AutoSenseProgress &progress,
                                               Clock::time_point deadline,
                                               bool allow_finish = false) {
  constexpr auto poll_interval = std::chrono::milliseconds(50);
  while (Clock::now() < deadline && !progress.completed) {
    if (interrupted != 0) {
      return std::unexpected(MidiError{.code = -EINTR, .operation = "Auto-Sens interrupted"});
    }
    if (allow_finish && finish_requested != 0 && progress.started) { return {}; }
    const auto until = deadline - Clock::now();
    auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(until);
    if (remaining < until) { ++remaining; }
    const auto timeout = std::clamp(remaining, std::chrono::milliseconds(1), poll_interval);
    const auto message = session.receive(timeout);
    if (!message) {
      if (message.error().code == -ETIMEDOUT) { continue; }
      return std::unexpected(message.error());
    }
    const auto updated = update_auto_sense(progress, *message);
    if (!updated) { return updated; }
  }
  return {};
}

std::expected<void, MidiError>
check_auto_sense_result(const Message &current, std::uint8_t selected, const Message &baseline) {
  if (!validate_bulk_write(current)) {
    return std::unexpected(MidiError{.code = -EBADMSG, .operation = "Auto-Sens result bounds"});
  }
  const auto preamps = decode_preamps(baseline);
  if (!preamps) {
    return std::unexpected(MidiError{.code = -EBADMSG, .operation = "Auto-Sens baseline"});
  }
  auto other_fields = current;
  constexpr std::size_t sensitivity_offset = 0x1d;
  for (std::size_t channel = 0; channel < preamps->channels.size(); ++channel) {
    if ((selected & (1U << channel)) != 0) {
      other_fields.payload[sensitivity_offset + channel] =
          baseline.payload[sensitivity_offset + channel];
    }
  }
  if (other_fields != baseline) {
    return std::unexpected(
        MidiError{.code = -EIO, .operation = "Auto-Sens unrelated state change"});
  }
  return {};
}

std::expected<Message, MidiError>
attempt_auto_sense(AutoSenseOptions request, ApplyState &state, AutoSenseProgress &progress) {
  auto session = MidiSession::open(state.options.port);
  if (!session) { return std::unexpected(session.error()); }
  const auto before = capture_snapshot(*session, state);
  if (!before) { return before; }
  state.write_attempted = true;
  const auto started = session->start_auto_sense(request.channels);
  if (!started) { return std::unexpected(started.error()); }
  const auto observed =
      wait_auto_sense(*session, progress, Clock::now() + request.duration, request.interactive);
  if (!observed) { return std::unexpected(observed.error()); }
  if (!progress.started) {
    return std::unexpected(
        MidiError{.code = -ETIMEDOUT, .operation = "Auto-Sens start notification"});
  }
  const auto stopped = session->stop_auto_sense(request.action);
  if (!stopped) { return std::unexpected(stopped.error()); }
  constexpr auto completion_timeout = std::chrono::seconds(3);
  const auto completed = wait_auto_sense(*session, progress, Clock::now() + completion_timeout);
  if (!completed) { return std::unexpected(completed.error()); }
  if (!progress.completed) {
    return std::unexpected(
        MidiError{.code = -ETIMEDOUT, .operation = "Auto-Sens completion notification"});
  }
  if (request.action == AutoSenseAction::cancel) { return session->write_bulk_state(*before); }
  auto current = session->read_bulk_state();
  if (!current) { return current; }
  const auto valid = check_auto_sense_result(*current, request.channels, *before);
  if (!valid) { return std::unexpected(valid.error()); }
  return current;
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

int initialize_settings(InitializationTarget target, ApplyOptions options) {
  if (!encode_initialization(target)) {
    return failure({.code = -EINVAL, .operation = "initialization target"});
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "initialization signal handlers"});
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  auto result = attempt_initialization(target, state);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "initialization interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, true); }
  std::cout << "Initialization command sent; state scope and repeated raw readback verified. "
               "Recovery snapshot: "
            << options.snapshot_path << '\n'
            << std::flush;
  return std::cout.good() ? 0 : 1;
}

int apply_direct_out(ApplyOptions options) {
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "Direct Out signal handlers"});
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  auto result = attempt_direct_out(state);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "Direct Out interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, true); }
  std::cout << "Direct Out applied; " << direct_out_change_count
            << " writes and complete raw readback verified. Recovery snapshot: "
            << options.snapshot_path << '\n'
            << std::flush;
  return std::cout.good() ? 0 : 1;
}

int apply_routing(RoutingPreset preset, ApplyOptions options) {
  if (preset != RoutingPreset::multi_monitor && preset != RoutingPreset::playback) {
    return failure({.code = -EINVAL, .operation = "routing preset"});
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "routing signal handlers"});
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  auto result = attempt_routing(preset, state);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "routing interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, true); }
  std::cout << "Routing preset applied; complete bulk readback verified after every route. "
               "Recovery snapshot: "
            << options.snapshot_path << '\n'
            << std::flush;
  return std::cout.good() ? 0 : 1;
}

int run_auto_sense(AutoSenseOptions request, ApplyOptions options) {
  constexpr auto maximum_duration = std::chrono::minutes(1);
  if (!encode_auto_sense_start(request.channels) || request.duration.count() <= 0 ||
      request.duration > maximum_duration || !encode_auto_sense_stop(request.action)) {
    return failure({.code = -EINVAL, .operation = "Auto-Sens options"});
  }
  if (!install_handlers(request.interactive)) {
    return failure({.code = -errno, .operation = "Auto-Sens signal handlers"});
  }
  if (request.interactive) {
    std::cout << "{\"event\":\"auto-sense-prepared\"}\n" << std::flush;
    if (!std::cout.good()) {
      return failure({.code = -EPIPE, .operation = "Auto-Sens progress output"});
    }
  }
  ApplyState state{.options = options, .snapshot = std::nullopt, .write_attempted = false};
  AutoSenseProgress progress{
      .active = {}, .selected = request.channels, .interactive = request.interactive};
  auto result = attempt_auto_sense(request, state, progress);
  if (result && interrupted != 0) {
    result = std::unexpected(MidiError{.code = -EINTR, .operation = "Auto-Sens interrupted"});
  }
  if (!result) { return failed_change(result.error(), state, true, true); }
  const auto preamps = decode_preamps(*result);
  if (!preamps) {
    return failed_change({.code = -EBADMSG, .operation = "Auto-Sens result"}, state, true, true);
  }
  std::cout << R"({"action":")" << (request.action == AutoSenseAction::finish ? "finish" : "cancel")
            << R"(","notifications":)" << progress.notifications << R"(,"sensitivity_indices":[)";
  for (const auto &channel : preamps->channels) {
    if (&channel != &preamps->channels.front()) { std::cout << ','; }
    std::cout << static_cast<unsigned int>(channel.sensitivity_index);
  }
  std::cout << "]}\n" << std::flush;
  return std::cout.good() ? 0 : 1;
}

int restore_snapshot(const Message &snapshot, std::string_view port, bool cancel_auto) {
  if (!validate_bulk_write(snapshot)) {
    std::cerr << "Recovery snapshot is invalid or outside known control bounds.\n";
    return 1;
  }
  if (!install_handlers()) {
    return failure({.code = -errno, .operation = "preset signal handlers"});
  }
  // Recovery is completed even if a graceful interruption arrives while it runs.
  const auto restored = recover(snapshot, port, cancel_auto);
  if (!restored) { return failure(restored.error()); }
  std::cout << "Raw state restored; complete bulk readback verified.\n" << std::flush;
  return std::cout.good() ? 0 : 1;
}

}
