// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Meters.hpp"
#include "octa/Midi.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>
#include <sys/poll.h>
#include <sys/types.h>
#include <unistd.h>
#include <vector>

namespace {

struct FakeIo {
  std::vector<std::uint8_t> incoming;
  std::vector<std::uint8_t> outgoing;
  std::size_t position = 0;
  std::size_t read_size = 1;
  std::size_t write_size = 3;
  unsigned int writes = 0;
  unsigned int closes = 0;
  unsigned int fail_write = 0;
  int open_error = 0;
  int read_error = 0;
};

FakeIo io;
std::max_align_t input_token{};
std::max_align_t output_token{};
std::array<int, 2> wake_pipe = {-1, -1};

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

std::vector<std::uint8_t> switch_frame(bool enabled) {
  const auto frame = octa::encode_meter_switch(enabled);
  return frame ? *frame : std::vector<std::uint8_t>{};
}

std::vector<std::uint8_t> level_frame(bool malformed = false) {
  constexpr std::size_t meter_bytes = 16;
  const std::vector<std::uint8_t> payload(malformed ? meter_bytes - 1 : meter_bytes);
  const auto frame = octa::encode(octa::Command::data, {0, 10, 0, 1}, payload);
  return frame ? *frame : std::vector<std::uint8_t>{};
}

bool sent_stop(std::size_t prefix) {
  const auto stop = switch_frame(false);
  return io.outgoing.size() == prefix + stop.size() &&
         std::ranges::equal(std::span(io.outgoing).subspan(prefix), stop);
}

bool successful_stream() {
  io = {};
  io.incoming = switch_frame(true); // Unrelated but valid notification.
  const auto levels = level_frame();
  io.incoming.insert(io.incoming.end(), levels.begin(), levels.end());
  unsigned int callbacks = 0;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [&callbacks](const octa::InputMeters &meters) {
        ++callbacks;
        return meters != octa::InputMeters{};
      });
  const auto start = switch_frame(true);
  return require(result && *result == 1 && callbacks == 1 && io.closes == 2 &&
                     sent_stop(start.size()) &&
                     std::ranges::equal(std::span(io.outgoing).first(start.size()), start),
                 "partial reads/writes, unrelated messages, callback cancellation and stop");
}

bool input_failure(bool malformed) {
  io = {};
  io.incoming = level_frame(malformed);
  io.read_error = malformed ? 0 : -EIO;
  unsigned int callbacks = 0;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [&callbacks](const octa::InputMeters &) {
        ++callbacks;
        return true;
      });
  const int expected = malformed ? -EBADMSG : -EIO;
  return require(!result && result.error().code == expected && callbacks == 0 && io.closes == 2 &&
                     sent_stop(switch_frame(true).size()),
                 "read errors and malformed packets still send the disable command");
}

bool write_failures() {
  io = {};
  io.fail_write = 2; // Fail after the first three bytes of the enable frame.
  const auto partial = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  if (!require(!partial && partial.error().code == -EIO && io.closes == 2 &&
                   sent_stop(io.write_size),
               "failed partial enable is followed by a complete disable attempt")) {
    return false;
  }
  io = {};
  io.incoming = level_frame();
  io.write_size = switch_frame(true).size();
  io.fail_write = 2; // The complete enable succeeds, then the stop fails.
  const auto stop = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  return require(!stop && stop.error().code == -EIO && io.writes == 2 && io.closes == 2,
                 "stop transmission failure is reported and both handles close");
}

bool deadlines() {
  constexpr auto normal_duration = std::chrono::milliseconds(50);
  constexpr auto empty_duration = std::chrono::milliseconds(20);
  io = {};
  io.read_size = level_frame().size();
  io.incoming = level_frame();
  unsigned int callbacks = 0;
  const auto normal = octa::observe_input_meters(
      "hw:fake", normal_duration, [&callbacks](const octa::InputMeters &) {
        ++callbacks;
        return true;
      });
  if (!require(normal && *normal == 1 && callbacks == 1 && io.closes == 2 &&
                   sent_stop(switch_frame(true).size()),
               "requested duration completes successfully after valid packets and an empty poll")) {
    return false;
  }
  io = {};
  const auto empty = octa::observe_input_meters(
      "hw:fake", empty_duration, [](const octa::InputMeters &) { return true; });
  return require(!empty && empty.error().code == -ETIMEDOUT && io.closes == 2 &&
                     sent_stop(switch_frame(true).size()),
                 "no packets is a timeout and still sends the stop command");
}

bool open_failure() {
  io = {};
  io.open_error = -EACCES;
  const auto result = octa::observe_input_meters(
      "hw:fake", std::chrono::seconds(1), [](const octa::InputMeters &) { return false; });
  return require(!result && result.error().code == -EACCES && io.writes == 0 && io.closes == 0,
                 "failed open never starts the meter stream");
}

}

// Test-only ALSA symbol implementations interpose the dynamically linked library.
// No hardware handle is opened. The real poll uses an empty pipe for timeouts.
extern "C" int snd_rawmidi_open(snd_rawmidi_t **in_rmidi,
                                snd_rawmidi_t **out_rmidi,
                                const char * /*name*/,
                                int /*mode*/) {
  if (io.open_error != 0) { return io.open_error; }
  const std::array handles{in_rmidi, out_rmidi};
  *handles.front() = reinterpret_cast<snd_rawmidi_t *>(&input_token);
  *handles.back() = reinterpret_cast<snd_rawmidi_t *>(&output_token);
  return 0;
}

extern "C" int snd_rawmidi_close(snd_rawmidi_t * /*rmidi*/) {
  ++io.closes;
  return 0;
}

extern "C" ssize_t snd_rawmidi_read(snd_rawmidi_t * /*rmidi*/, void *buffer, std::size_t size) {
  if (io.read_error != 0) { return io.read_error; }
  if (io.position == io.incoming.size()) { return -EAGAIN; }
  const auto count = std::min({size, io.read_size, io.incoming.size() - io.position});
  std::ranges::copy(std::span(io.incoming).subspan(io.position, count),
                    static_cast<std::uint8_t *>(buffer));
  io.position += count;
  return static_cast<ssize_t>(count);
}

extern "C" ssize_t
snd_rawmidi_write(snd_rawmidi_t * /*rmidi*/, const void *buffer, std::size_t size) {
  ++io.writes;
  if (io.writes == io.fail_write) { return -EIO; }
  const auto count = std::min(size, io.write_size);
  const std::span bytes(static_cast<const std::uint8_t *>(buffer), count);
  io.outgoing.insert(io.outgoing.end(), bytes.begin(), bytes.end());
  return static_cast<ssize_t>(count);
}

extern "C" int snd_rawmidi_poll_descriptors_count(snd_rawmidi_t * /*rmidi*/) {
  return 1;
}

extern "C" int
snd_rawmidi_poll_descriptors(snd_rawmidi_t * /*rmidi*/, struct pollfd *pfds, unsigned int space) {
  if (space != 1) { return -EINVAL; }
  *pfds = {.fd = wake_pipe.front(), .events = POLLIN, .revents = 0};
  return 1;
}

extern "C" int snd_rawmidi_poll_descriptors_revents(snd_rawmidi_t * /*rawmidi*/,
                                                    struct pollfd *pfds,
                                                    unsigned int nfds,
                                                    unsigned short *revent) {
  if (nfds != 1) { return -EINVAL; }
  *revent = static_cast<unsigned short>(pfds->revents);
  return 0;
}

int main() {
  if (pipe(wake_pipe.data()) != 0) { return 1; }
  const bool passed = successful_stream() && input_failure(false) && input_failure(true) &&
                      write_failures() && deadlines() && open_failure();
  const int read_closed = close(wake_pipe.front());
  const int write_closed = close(wake_pipe.back());
  return passed && read_closed == 0 && write_closed == 0 ? 0 : 1;
}
