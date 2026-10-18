// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"

#include "octa/AutoSense.hpp"
#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Initialization.hpp"
#include "octa/Meters.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <iostream>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <sys/poll.h>
#include <utility>
#include <vector>

namespace octa {
namespace {

using Clock = std::chrono::steady_clock;
using RawMidi = std::unique_ptr<snd_rawmidi_t, decltype(&snd_rawmidi_close)>;
constexpr auto query_timeout = std::chrono::seconds(3);
constexpr int maximum_poll_descriptors = 32;
constexpr std::size_t input_chunk_size = 512;

struct Connection {
  RawMidi input;
  RawMidi output;
};

std::expected<Connection, MidiError> open_ctrl(std::string_view port) {
  if (!port.starts_with("hw:") || port.find('\0') != std::string_view::npos) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "hardware MIDI port"});
  }
  const std::string name(port);
  snd_rawmidi_t *raw_input = nullptr;
  snd_rawmidi_t *raw_output = nullptr;
  const int opened = snd_rawmidi_open(&raw_input, &raw_output, name.c_str(), SND_RAWMIDI_NONBLOCK);
  Connection connection = {.input = RawMidi(raw_input, &snd_rawmidi_close),
                           .output = RawMidi(raw_output, &snd_rawmidi_close)};
  if (opened < 0) { return std::unexpected(MidiError{.code = opened, .operation = "open CTRL"}); }
  return connection;
}

std::expected<void, MidiError> wait_ready(snd_rawmidi_t *handle, Clock::time_point deadline) {
  const int count = snd_rawmidi_poll_descriptors_count(handle);
  if (count <= 0 || count > maximum_poll_descriptors) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "poll descriptors"});
  }
  std::vector<pollfd> descriptors(static_cast<std::size_t>(count));
  const auto descriptor_count = static_cast<unsigned int>(count);
  const int filled = snd_rawmidi_poll_descriptors(handle, descriptors.data(), descriptor_count);
  if (filled != count) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "poll descriptors"});
  }
  while (true) {
    const auto until = deadline - Clock::now();
    if (until.count() <= 0) {
      return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "MIDI readiness"});
    }
    auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(until);
    // poll takes integer milliseconds; rounding down would expire early.
    if (remaining < until) { ++remaining; }
    const int polled =
        poll(descriptors.data(), descriptors.size(), static_cast<int>(remaining.count()));
    if (polled < 0 && errno == EINTR) { continue; }
    if (polled < 0) { return std::unexpected(MidiError{.code = -errno, .operation = "poll"}); }
    if (polled == 0) { continue; }
    unsigned short events = 0;
    const int result =
        snd_rawmidi_poll_descriptors_revents(handle, descriptors.data(), descriptor_count, &events);
    if (result < 0) {
      return std::unexpected(MidiError{.code = result, .operation = "poll events"});
    }
    if ((events & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
      return std::unexpected(MidiError{.code = -EIO, .operation = "MIDI disconnected"});
    }
    if ((events & (POLLIN | POLLOUT)) != 0) { return {}; }
  }
}

std::expected<void, MidiError> send_frame(snd_rawmidi_t *output,
                                          std::span<const std::uint8_t> pending,
                                          Clock::time_point deadline) {
  while (!pending.empty()) {
    if (Clock::now() >= deadline) {
      return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "send request"});
    }
    const auto written = snd_rawmidi_write(output, pending.data(), pending.size());
    if (written == -EINTR) { continue; }
    if (written == -EAGAIN) {
      const auto ready = wait_ready(output, deadline);
      if (!ready) { return ready; }
      continue;
    }
    if (written <= 0) {
      return std::unexpected(MidiError{.code = written < 0 ? static_cast<int>(written) : -EIO,
                                       .operation = "send request"});
    }
    const auto count = static_cast<std::size_t>(written);
    if (count > pending.size()) {
      return std::unexpected(MidiError{.code = -EIO, .operation = "MIDI write size"});
    }
    pending = pending.subspan(count);
  }
  return {};
}

std::expected<std::size_t, MidiError> read_chunk(snd_rawmidi_t *input,
                                                 std::span<std::uint8_t> buffer) {
  const auto received = snd_rawmidi_read(input, buffer.data(), buffer.size());
  if (received <= 0) {
    return std::unexpected(MidiError{.code = received < 0 ? static_cast<int>(received) : -EIO,
                                     .operation = "read MIDI"});
  }
  const auto count = static_cast<std::size_t>(received);
  if (count > buffer.size()) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "MIDI read size"});
  }
  return count;
}

template <typename Transact>
std::expected<Message, MidiError>
write_bulk_verified(const Message &target, const Message &retained, const Transact &transact) {
  if (!validate_bulk_write(target)) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "bulk write bounds"});
  }
  const auto frame = encode(target.command, target.address, target.payload);
  if (!frame) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "bulk write frame"});
  }
  auto reply = transact(*frame);
  if (!reply) { return reply; }
  if (*reply != target && *reply != retained) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "bulk readback"});
  }
  return reply;
}

class ActiveOperation {
public:
  explicit ActiveOperation(bool &active) : active_(active) { active_ = true; }

  ~ActiveOperation() { active_ = false; }

  ActiveOperation(const ActiveOperation &) = delete;
  ActiveOperation &operator=(const ActiveOperation &) = delete;
  ActiveOperation(ActiveOperation &&) = delete;
  ActiveOperation &operator=(ActiveOperation &&) = delete;

private:
  bool &active_;
};

std::expected<std::size_t, MidiError>
receive_meters(MidiSession &session, Clock::time_point end, const MeterListener &listener) {
  std::size_t packets = 0;
  auto active_until = Clock::now() + query_timeout;
  while (Clock::now() < end) {
    const auto until = std::min(end, active_until) - Clock::now();
    auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(until);
    if (remaining < until) { ++remaining; }
    auto message = session.receive(std::max(remaining, std::chrono::milliseconds(1)));
    if (!message) {
      if (message.error().code == -ETIMEDOUT && Clock::now() >= end && packets != 0) {
        return packets;
      }
      return std::unexpected(message.error());
    }
    if (message->command != Command::data || message->address != Address{0, 10, 0, 1}) { continue; }
    const auto meters = decode_input_meters(*message);
    if (!meters) {
      return std::unexpected(MidiError{.code = -EBADMSG, .operation = "meter packet"});
    }
    ++packets;
    active_until = Clock::now() + query_timeout;
    if (!listener(*meters) || Clock::now() >= end) { return packets; }
  }
  if (packets == 0) {
    return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "meter reply"});
  }
  return packets;
}

}

struct MidiSession::Impl {
  explicit Impl(Connection opened) : connection(std::move(opened)) {}

  ~Impl() {
    if (auto_sense_may_be_active) {
      if (const auto stopped = stop_auto_sense(AutoSenseAction::cancel); !stopped) {
        std::cerr << "OCTA-CAPTURE Auto-Sens cleanup failed: " << stopped.error().operation << " ("
                  << stopped.error().code << ")\n";
      }
    }
    if (meters_may_be_active) {
      if (const auto stopped = switch_meters(false); !stopped) {
        std::cerr << "OCTA-CAPTURE meter cleanup failed: " << stopped.error().operation << " ("
                  << stopped.error().code << ")\n";
      }
    }
  }

  Impl(const Impl &) = delete;
  Impl &operator=(const Impl &) = delete;
  Impl(Impl &&) = delete;
  Impl &operator=(Impl &&) = delete;

  std::expected<void, MidiError> switch_meters(bool enabled) {
    const auto frame = encode_meter_switch(enabled);
    if (!frame) {
      return std::unexpected(MidiError{.code = -EINVAL, .operation = "meter switch encoding"});
    }
    if (enabled) { meters_may_be_active = true; }
    const auto sent = send_frame(connection.output.get(), *frame, Clock::now() + query_timeout);
    if (sent && !enabled) { meters_may_be_active = false; }
    return sent;
  }

  std::expected<void, MidiError> stop_auto_sense(AutoSenseAction action) {
    const auto frame = encode_auto_sense_stop(action);
    if (!frame) {
      return std::unexpected(MidiError{.code = -EINVAL, .operation = "Auto-Sens action"});
    }
    const auto sent = send_frame(connection.output.get(), *frame, Clock::now() + query_timeout);
    if (sent) { auto_sense_may_be_active = false; }
    return sent;
  }

  std::expected<std::optional<Message>, MidiError> buffered_message() {
    while (position < length) {
      const auto byte = bytes[position++];
      constexpr std::uint8_t start_exclusive = 0xf0;
      if (byte == start_exclusive) { frame_is_current = request_pending; }
      auto decoded = receiver.feed(byte);
      if (!decoded) {
        return std::unexpected(MidiError{.code = -EBADMSG, .operation = "MIDI envelope"});
      }
      auto &message = *decoded;
      if (message.has_value()) { return std::move(message); }
    }
    return std::nullopt;
  }

  std::expected<Message, MidiError> next_message(Clock::time_point deadline) {
    while (Clock::now() < deadline) {
      auto decoded = buffered_message();
      if (!decoded) { return std::unexpected(decoded.error()); }
      auto &message = *decoded;
      if (message.has_value()) { return std::move(*message); }
      const auto read = read_chunk(connection.input.get(), bytes);
      if (read) {
        position = 0;
        length = *read;
      } else if (read.error().code == -EAGAIN) {
        const auto ready = wait_ready(connection.input.get(), deadline);
        if (!ready) { return std::unexpected(ready.error()); }
      } else if (read.error().code != -EINTR) {
        return std::unexpected(read.error());
      }
    }
    return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "MIDI reply"});
  }

  std::expected<void, MidiError> drain(const MessageListener &listener,
                                       Clock::time_point deadline) {
    while (Clock::now() < deadline) {
      auto decoded = buffered_message();
      if (!decoded) { return std::unexpected(decoded.error()); }
      const auto &message = *decoded;
      if (message.has_value()) {
        if (listener) { listener(*message); }
        continue;
      }
      const auto read = read_chunk(connection.input.get(), bytes);
      if (read) {
        position = 0;
        length = *read;
      } else if (read.error().code == -EAGAIN) {
        return {};
      } else if (read.error().code != -EINTR) {
        return std::unexpected(read.error());
      }
    }
    return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "drain MIDI"});
  }

  Connection connection;
  SysExReceiver receiver;
  std::array<std::uint8_t, input_chunk_size> bytes{};
  std::size_t position = 0;
  std::size_t length = 0;
  bool busy = false;
  bool meters_may_be_active = false;
  bool auto_sense_may_be_active = false;
  bool request_pending = false;
  bool frame_is_current = false;
  bool query_uncertain = false;
};

MidiSession::MidiSession(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

MidiSession::~MidiSession() = default;
MidiSession::MidiSession(MidiSession &&) noexcept = default;
MidiSession &MidiSession::operator=(MidiSession &&) noexcept = default;

std::expected<MidiSession, MidiError> MidiSession::open(std::string_view port) {
  auto opened = open_ctrl(port);
  if (!opened) { return std::unexpected(opened.error()); }
  return MidiSession(std::make_unique<Impl>(std::move(*opened)));
}

std::expected<void, MidiError> MidiSession::set_input_meters(bool enabled) {
  if (!impl_) { return std::unexpected(MidiError{.code = -ENODEV, .operation = "closed session"}); }
  if (impl_->busy) {
    return std::unexpected(MidiError{.code = -EBUSY, .operation = "busy session"});
  }
  const ActiveOperation active(impl_->busy);
  return impl_->switch_meters(enabled);
}

std::expected<void, MidiError> MidiSession::start_auto_sense(std::uint8_t channels) {
  const auto frame = encode_auto_sense_start(channels);
  if (!frame) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "Auto-Sens channels"});
  }
  if (!impl_) { return std::unexpected(MidiError{.code = -ENODEV, .operation = "closed session"}); }
  if (impl_->busy) {
    return std::unexpected(MidiError{.code = -EBUSY, .operation = "busy session"});
  }
  if (impl_->query_uncertain) {
    return std::unexpected(MidiError{.code = -EPROTO, .operation = "unresolved CTRL query"});
  }
  if (impl_->auto_sense_may_be_active) {
    return std::unexpected(MidiError{.code = -EALREADY, .operation = "Auto-Sens already started"});
  }
  const ActiveOperation active(impl_->busy);
  impl_->auto_sense_may_be_active = true;
  return send_frame(impl_->connection.output.get(), *frame, Clock::now() + query_timeout);
}

std::expected<void, MidiError> MidiSession::stop_auto_sense(AutoSenseAction action) {
  if (action != AutoSenseAction::finish && action != AutoSenseAction::cancel) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "Auto-Sens action"});
  }
  if (!impl_) { return std::unexpected(MidiError{.code = -ENODEV, .operation = "closed session"}); }
  if (impl_->busy) {
    return std::unexpected(MidiError{.code = -EBUSY, .operation = "busy session"});
  }
  const ActiveOperation active(impl_->busy);
  return impl_->stop_auto_sense(action);
}

std::expected<Message, MidiError> MidiSession::receive(std::chrono::milliseconds timeout) {
  constexpr auto maximum_timeout = std::chrono::minutes(1);
  if (timeout.count() <= 0 || timeout > maximum_timeout) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "MIDI timeout"});
  }
  if (!impl_) { return std::unexpected(MidiError{.code = -ENODEV, .operation = "closed session"}); }
  if (impl_->busy) {
    return std::unexpected(MidiError{.code = -EBUSY, .operation = "busy session"});
  }
  const ActiveOperation active(impl_->busy);
  return impl_->next_message(Clock::now() + timeout);
}

std::expected<Message, MidiError> MidiSession::query(std::span<const std::uint8_t> update,
                                                     const MessageListener &listener) {
  if (!impl_) { return std::unexpected(MidiError{.code = -ENODEV, .operation = "closed session"}); }
  if (impl_->busy) {
    return std::unexpected(MidiError{.code = -EBUSY, .operation = "busy session"});
  }
  if (impl_->query_uncertain) {
    return std::unexpected(MidiError{.code = -EPROTO, .operation = "unresolved CTRL query"});
  }
  const ActiveOperation active(impl_->busy);
  const auto deadline = Clock::now() + query_timeout;
  impl_->frame_is_current = false;
  const auto drained = impl_->drain(listener, deadline);
  if (!drained) { return std::unexpected(drained.error()); }
  impl_->query_uncertain = true;
  if (!update.empty()) {
    const auto sent = send_frame(impl_->connection.output.get(), update, deadline);
    if (!sent) { return std::unexpected(sent.error()); }
  }
  const auto frame = request(bulk_address, bulk_size);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "encode request"}); }
  const auto sent = send_frame(impl_->connection.output.get(), *frame, deadline);
  if (!sent) { return std::unexpected(sent.error()); }
  const ActiveOperation pending(impl_->request_pending);
  while (Clock::now() < deadline) {
    auto message = impl_->next_message(deadline);
    if (!message) { return message; }
    if (impl_->frame_is_current && message->command == Command::data &&
        message->address == bulk_address && message->payload.size() == bulk_size) {
      impl_->query_uncertain = false;
      return message;
    }
    if (listener) { listener(*message); }
  }
  return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "state reply"});
}

std::expected<Message, MidiError> MidiSession::read_bulk_state(const MessageListener &listener) {
  return query({}, listener);
}

std::expected<Message, MidiError> MidiSession::panel_command(PanelCommand command,
                                                             const MessageListener &listener) {
  const auto frame = encode_panel_command(command);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "panel command"}); }
  const auto before = query({}, listener);
  if (!before) { return before; }
  if (!validate_bulk_write(*before)) {
    return std::unexpected(MidiError{.code = -EBADMSG, .operation = "panel command state"});
  }
  constexpr std::size_t rate_offset = 2;
  constexpr std::uint8_t highest_rate = 3;
  if (command.action == PanelAction::select_mixer && command.bus != 0 &&
      before->payload[rate_offset] == highest_rate) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "192 kHz mixer selection"});
  }
  auto after = query(*frame, listener);
  if (after && !panel_command_preserves_state(*before, *after, command)) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "panel audio-state preservation"});
  }
  return after;
}

std::expected<Message, MidiError> MidiSession::write_preamp(PreampChange change,
                                                            const MessageListener &listener) {
  const auto frame = encode_preamp_change(change);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "preamp value"}); }
  auto state = query(*frame, listener);
  if (!state) { return state; }
  const auto bank = decode_preamps(*state);
  if (!bank) { return std::unexpected(MidiError{.code = -EBADMSG, .operation = "preamp state"}); }
  const auto observed = preamp_value(*bank, change.channel, change.parameter);
  if (!observed || *observed != change.value) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "preamp readback"});
  }
  return state;
}

std::expected<Message, MidiError> MidiSession::write_dsp(const DspChange &change,
                                                         const MessageListener &listener) {
  const auto frame = encode_dsp_change(change);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "DSP value"}); }
  auto reply = query(*frame, listener);
  if (!reply) { return reply; }
  const auto state = decode_dsp(*reply);
  if (!state) { return std::unexpected(MidiError{.code = -EBADMSG, .operation = "DSP state"}); }
  const auto matches = matches_dsp_change(*state, change);
  if (!matches || !*matches) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "DSP readback"});
  }
  return reply;
}

std::expected<Message, MidiError> MidiSession::operate_memory(MemoryAction action,
                                                              const MessageListener &listener) {
  if (action != MemoryAction::store && action != MemoryAction::load) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "memory action"});
  }
  constexpr Address address = {0, 0, 0, 1};
  const std::array payload = {static_cast<std::uint8_t>(action)};
  const auto frame = encode(Command::data, address, payload);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "memory frame"}); }
  return query(*frame, listener);
}

std::expected<Message, MidiError>
MidiSession::initialize_settings(InitializationTarget target, const MessageListener &listener) {
  const auto frame = encode_initialization(target);
  if (!frame) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "initialization target"});
  }
  return query(*frame, listener);
}

std::expected<Message, MidiError> MidiSession::write_bulk_state(const Message &target,
                                                                const MessageListener &listener) {
  return write_bulk_verified(
      target, target, [this, &listener](std::span<const std::uint8_t> frame) {
        return query(frame, listener);
      });
}

std::expected<Message, MidiError> MidiSession::write_preset(const Preset &preset,
                                                            const Message &current,
                                                            const MessageListener &listener) {
  const auto target = preset_to_bulk(preset, current);
  if (!target) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "preset write bounds"});
  }
  auto retained = *target;
  constexpr std::size_t rate_offset = 2;
  constexpr std::size_t metadata_offset = 4;
  constexpr std::uint8_t highest_rate = 3;
  // The reference sends zero here at 192 kHz; firmware may retain this metadata.
  // Both complete messages are checked, so every other byte must still match.
  if (current.payload[rate_offset] == highest_rate) {
    retained.payload[metadata_offset] = current.payload[metadata_offset];
  }
  return write_bulk_verified(
      *target, retained, [this, &listener](std::span<const std::uint8_t> frame) {
        return query(frame, listener);
      });
}

std::expected<Message, MidiError> read_bulk_state(std::string_view port) {
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  return session->read_bulk_state();
}

std::expected<Message, MidiError> write_preamp(std::string_view port, PreampChange change) {
  if (!encode_preamp_change(change)) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "preamp value"});
  }
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  return session->write_preamp(change);
}

std::expected<Message, MidiError> write_dsp(std::string_view port, const DspChange &change) {
  if (!encode_dsp_change(change)) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "DSP value"});
  }
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  return session->write_dsp(change);
}

std::expected<std::size_t, MidiError> observe_input_meters(std::string_view port,
                                                           std::chrono::milliseconds duration,
                                                           const MeterListener &listener) {
  constexpr auto maximum_duration = std::chrono::minutes(1);
  if (duration.count() <= 0 || duration > maximum_duration) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "meter duration"});
  }
  if (!listener) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "meter callback"});
  }
  auto session = MidiSession::open(port);
  if (!session) { return std::unexpected(session.error()); }
  const auto end = Clock::now() + duration;
  const auto enabled = session->set_input_meters(true);
  const auto result = enabled
                          ? receive_meters(*session, end, listener)
                          : std::expected<std::size_t, MidiError>(std::unexpected(enabled.error()));
  const auto disabled = session->set_input_meters(false);
  if (!disabled) { return std::unexpected(disabled.error()); }
  return result;
}

}
