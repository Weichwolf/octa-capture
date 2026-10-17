// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"

#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <memory>
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
    pending = pending.subspan(static_cast<std::size_t>(written));
  }
  return {};
}

std::expected<Message, MidiError> receive_state(snd_rawmidi_t *input, Clock::time_point deadline) {
  SysExReceiver receiver;
  std::array<std::uint8_t, input_chunk_size> bytes{};
  while (Clock::now() < deadline) {
    const auto received = snd_rawmidi_read(input, bytes.data(), bytes.size());
    if (received == -EINTR) { continue; }
    if (received == -EAGAIN) {
      const auto ready = wait_ready(input, deadline);
      if (!ready) { return std::unexpected(ready.error()); }
      continue;
    }
    if (received <= 0) {
      return std::unexpected(MidiError{.code = received < 0 ? static_cast<int>(received) : -EIO,
                                       .operation = "read state"});
    }
    for (const auto byte : std::span(bytes).first(static_cast<std::size_t>(received))) {
      auto message = receiver.feed(byte);
      if (!message) {
        return std::unexpected(MidiError{.code = -EBADMSG, .operation = "decode state"});
      }
      auto &candidate = *message;
      if (candidate.has_value() && candidate->command == Command::data &&
          candidate->address == bulk_address && candidate->payload.size() == bulk_size) {
        return std::move(*candidate);
      }
    }
  }
  return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "state reply"});
}

std::expected<Message, MidiError> query_state(std::string_view port,
                                              std::span<const std::uint8_t> update) {
  auto connection = open_ctrl(port);
  if (!connection) { return std::unexpected(connection.error()); }
  const auto deadline = Clock::now() + query_timeout;
  if (!update.empty()) {
    const auto sent = send_frame(connection->output.get(), update, deadline);
    if (!sent) { return std::unexpected(sent.error()); }
  }
  const auto frame = request(bulk_address, bulk_size);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "encode request"}); }
  const auto sent = send_frame(connection->output.get(), *frame, deadline);
  if (!sent) { return std::unexpected(sent.error()); }
  return receive_state(connection->input.get(), deadline);
}

std::expected<std::size_t, MidiError>
receive_bytes(snd_rawmidi_t *input, std::span<std::uint8_t> buffer, Clock::time_point deadline) {
  while (Clock::now() < deadline) {
    const auto received = snd_rawmidi_read(input, buffer.data(), buffer.size());
    if (received == -EINTR) { continue; }
    if (received == -EAGAIN) {
      const auto ready = wait_ready(input, deadline);
      if (!ready) { return std::unexpected(ready.error()); }
      continue;
    }
    if (received <= 0) {
      return std::unexpected(MidiError{.code = received < 0 ? static_cast<int>(received) : -EIO,
                                       .operation = "read meters"});
    }
    const auto count = static_cast<std::size_t>(received);
    if (count > buffer.size()) {
      return std::unexpected(MidiError{.code = -EIO, .operation = "MIDI read size"});
    }
    return count;
  }
  return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "meter reply"});
}

std::expected<std::size_t, MidiError>
receive_meters(snd_rawmidi_t *input, Clock::time_point end, const MeterListener &listener) {
  MeterReceiver receiver;
  std::array<std::uint8_t, input_chunk_size> bytes{};
  std::size_t packets = 0;
  auto active_until = Clock::now() + query_timeout;
  while (Clock::now() < end) {
    const auto count = receive_bytes(input, bytes, std::min(end, active_until));
    if (!count) {
      if (count.error().code == -ETIMEDOUT && Clock::now() >= end && packets != 0) {
        return packets;
      }
      return std::unexpected(count.error());
    }
    for (const auto byte : std::span(bytes).first(*count)) {
      const auto result = receiver.feed(byte);
      if (!result) {
        return std::unexpected(MidiError{.code = -EBADMSG, .operation = "meter envelope"});
      }
      const auto &meters = *result;
      if (!meters.has_value()) { continue; }
      ++packets;
      active_until = Clock::now() + query_timeout;
      if (!listener(*meters) || Clock::now() >= end) { return packets; }
    }
  }
  if (packets == 0) {
    return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "meter reply"});
  }
  return packets;
}

}

std::expected<Message, MidiError> read_bulk_state(std::string_view port) {
  return query_state(port, {});
}

std::expected<Message, MidiError> write_preamp(std::string_view port, PreampChange change) {
  const auto frame = encode_preamp_change(change);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "preamp value"}); }
  auto state = query_state(port, *frame);
  if (!state) { return state; }
  const auto bank = decode_preamps(*state);
  if (!bank) { return std::unexpected(MidiError{.code = -EBADMSG, .operation = "preamp state"}); }
  const auto observed = preamp_value(*bank, change.channel, change.parameter);
  if (!observed || *observed != change.value) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "preamp readback"});
  }
  return state;
}

std::expected<Message, MidiError> write_dsp(std::string_view port, const DspChange &change) {
  const auto frame = encode_dsp_change(change);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "DSP value"}); }
  auto reply = query_state(port, *frame);
  if (!reply) { return reply; }
  const auto state = decode_dsp(*reply);
  if (!state) { return std::unexpected(MidiError{.code = -EBADMSG, .operation = "DSP state"}); }
  const auto matches = matches_dsp_change(*state, change);
  if (!matches || !*matches) {
    return std::unexpected(MidiError{.code = -EIO, .operation = "DSP readback"});
  }
  return reply;
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
  const auto enable = encode_meter_switch(true);
  const auto disable = encode_meter_switch(false);
  if (!enable || !disable) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "meter switch encoding"});
  }
  auto connection = open_ctrl(port);
  if (!connection) { return std::unexpected(connection.error()); }
  const auto end = Clock::now() + duration;
  const auto enabled = send_frame(connection->output.get(), *enable, Clock::now() + query_timeout);
  const auto result = enabled
                          ? receive_meters(connection->input.get(), end, listener)
                          : std::expected<std::size_t, MidiError>(std::unexpected(enabled.error()));
  // The stop command is attempted even after a failed/partial start or malformed input.
  const auto disabled =
      send_frame(connection->output.get(), *disable, Clock::now() + query_timeout);
  if (!disabled) { return std::unexpected(disabled.error()); }
  return result;
}

}
