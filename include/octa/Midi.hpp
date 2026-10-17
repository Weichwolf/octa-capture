// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/AutoSense.hpp"
#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <functional>
#include <memory>
#include <span>
#include <string_view>

namespace octa {

enum class MemoryAction : std::uint8_t { store, load };

struct MidiError {
  int code;
  std::string_view operation;
};

// Notifications encountered while awaiting a bulk reply; callbacks must be short.
using MessageListener = std::function<void(const Message &)>;

// Thread-confined owner of one CTRL connection. Serializes queries and meter
// traffic without reopening the port. Reentrant operations return -EBUSY.
// Callbacks must not move/destroy this session. After an unresolved query,
// queries return -EPROTO until reopened; meter disable remains available.
class MidiSession {
public:
  [[nodiscard]] static std::expected<MidiSession, MidiError> open(std::string_view port);
  // Best-effort Auto-Sens cancel and meter disable before releasing the
  // connection. Explicit stop/disable reports cleanup errors.
  ~MidiSession();
  MidiSession(MidiSession &&) noexcept;
  MidiSession &operator=(MidiSession &&) noexcept;
  MidiSession(const MidiSession &) = delete;
  MidiSession &operator=(const MidiSession &) = delete;

  [[nodiscard]] std::expected<void, MidiError> set_input_meters(bool enabled);
  // Successful transmission does not confirm activity/completion. Receive
  // notifications separately. A failed start still requires cancel/cleanup.
  [[nodiscard]] std::expected<void, MidiError> start_auto_sense(std::uint8_t channels);
  [[nodiscard]] std::expected<void, MidiError> stop_auto_sense(AutoSenseAction action);

  // Preserves partial frames and unread bytes across calls. Timeout: 1..60000 ms.
  [[nodiscard]] std::expected<Message, MidiError> receive(std::chrono::milliseconds timeout);
  [[nodiscard]] std::expected<Message, MidiError>
  read_bulk_state(const MessageListener &listener = {});
  [[nodiscard]] std::expected<Message, MidiError>
  write_preamp(PreampChange change, const MessageListener &listener = {});
  [[nodiscard]] std::expected<Message, MidiError> write_dsp(const DspChange &change,
                                                            const MessageListener &listener = {});

  // Sends Store/Load Memory and queries current state. The reply does not prove
  // persistent storage or completion of a load; callers must verify separately.
  [[nodiscard]] std::expected<Message, MidiError>
  operate_memory(MemoryAction action, const MessageListener &listener = {});

  // Validate before MIDI I/O; require an exact fresh bulk readback. Raw writes
  // preserve snapshot fields omitted from .octa files for complete recovery.
  [[nodiscard]] std::expected<Message, MidiError>
  write_bulk_state(const Message &target, const MessageListener &listener = {});
  // Read current on this same connection and keep a recovery snapshot first.
  [[nodiscard]] std::expected<Message, MidiError>
  write_preset(const Preset &preset, const Message &current, const MessageListener &listener = {});

private:
  struct Impl;
  explicit MidiSession(std::unique_ptr<Impl> impl);
  [[nodiscard]] std::expected<Message, MidiError> query(std::span<const std::uint8_t> update,
                                                        const MessageListener &listener);
  std::unique_ptr<Impl> impl_;
};

// Deadline covers write and reply.
[[nodiscard]] std::expected<Message, MidiError> read_bulk_state(std::string_view port);
// Validates before opening the port, writes DT1, then verifies a fresh bulk reply.
[[nodiscard]] std::expected<Message, MidiError> write_preamp(std::string_view port,
                                                             PreampChange change);

[[nodiscard]] std::expected<Message, MidiError> write_dsp(std::string_view port,
                                                          const DspChange &change);

// A listener returns false to end observation. Keep callbacks short/nonblocking.
using MeterListener = std::function<bool(const InputMeters &)>;
// One CTRL connection, bounded duration (1..60000 ms), three-second inactivity
// timeout. Attempts the disable command on every exit after enabling was tried.
[[nodiscard]] std::expected<std::size_t, MidiError> observe_input_meters(
    std::string_view port, std::chrono::milliseconds duration, const MeterListener &listener);

}
