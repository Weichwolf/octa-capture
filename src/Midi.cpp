// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"

#include "octa/Protocol.hpp"

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
    const auto remaining =
        std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
    if (remaining.count() <= 0) {
      return std::unexpected(MidiError{.code = -ETIMEDOUT, .operation = "state reply"});
    }
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

std::expected<void, MidiError> send_request(snd_rawmidi_t *output, Clock::time_point deadline) {
  const auto frame = request(bulk_address, bulk_size);
  if (!frame) { return std::unexpected(MidiError{.code = -EINVAL, .operation = "encode request"}); }
  auto pending = std::span(*frame);
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

}

std::expected<Message, MidiError> read_bulk_state(std::string_view port) {
  if (!port.starts_with("hw:") || port.find('\0') != std::string_view::npos) {
    return std::unexpected(MidiError{.code = -EINVAL, .operation = "hardware MIDI port"});
  }
  const std::string name(port);
  snd_rawmidi_t *raw_input = nullptr;
  snd_rawmidi_t *raw_output = nullptr;
  const int opened = snd_rawmidi_open(&raw_input, &raw_output, name.c_str(), SND_RAWMIDI_NONBLOCK);
  const RawMidi input(raw_input, &snd_rawmidi_close);
  const RawMidi output(raw_output, &snd_rawmidi_close);
  if (opened < 0) { return std::unexpected(MidiError{.code = opened, .operation = "open CTRL"}); }
  const auto deadline = Clock::now() + query_timeout;
  const auto sent = send_request(output.get(), deadline);
  if (!sent) { return std::unexpected(sent.error()); }
  return receive_state(input.get(), deadline);
}

}
