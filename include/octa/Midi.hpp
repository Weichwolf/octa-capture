// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Preamp.hpp"
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
  // Best-effort meter disable before releasing the connection. Use explicit
  // disable to receive any cleanup error.
  ~MidiSession();
  MidiSession(MidiSession &&) noexcept;
  MidiSession &operator=(MidiSession &&) noexcept;
  MidiSession(const MidiSession &) = delete;
  MidiSession &operator=(const MidiSession &) = delete;

  [[nodiscard]] std::expected<void, MidiError> set_input_meters(bool enabled);
  // Preserves partial frames and unread bytes across calls. Timeout: 1..60000 ms.
  [[nodiscard]] std::expected<Message, MidiError> receive(std::chrono::milliseconds timeout);
  [[nodiscard]] std::expected<Message, MidiError>
  read_bulk_state(const MessageListener &listener = {});
  [[nodiscard]] std::expected<Message, MidiError>
  write_preamp(PreampChange change, const MessageListener &listener = {});
  [[nodiscard]] std::expected<Message, MidiError> write_dsp(const DspChange &change,
                                                            const MessageListener &listener = {});

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
