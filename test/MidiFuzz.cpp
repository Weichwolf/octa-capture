// SPDX-License-Identifier: GPL-2.0-only
#include "octa/AutoSense.hpp"
#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Midi.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <span>
#include <sys/poll.h>
#include <sys/types.h>

namespace {

struct FakeIo {
  std::span<const std::uint8_t> bytes;
  std::size_t position = 0;
  std::size_t read_size = 1;
  std::size_t write_size = 1;
  std::size_t writes = 0;
  unsigned int closes = 0;
  octa::SysExReceiver requests;
};

FakeIo io;
std::max_align_t input_token{};
std::max_align_t output_token{};

void require(bool condition) {
  if (!condition) { __builtin_trap(); }
}

void check_message(const octa::Message &message) {
  const auto encoded = octa::encode(message.command, message.address, message.payload);
  require(encoded.has_value());
  if (!encoded) { return; }
  const auto decoded = octa::decode(*encoded);
  require(decoded && *decoded == message);
}

void exercise_auto_sense(octa::MidiSession &session) {
  const std::uint8_t selected = io.bytes.empty() ? std::uint8_t{1} : io.bytes.front();
  const auto started = session.start_auto_sense(selected);
  if (selected == 0) {
    require(!started && started.error().code == -EINVAL);
  } else {
    require(started.has_value());
  }
  const auto action = static_cast<octa::AutoSenseAction>(io.bytes.empty() ? 0 : io.bytes.back());
  const auto writes = io.writes;
  const auto stopped = session.stop_auto_sense(action);
  if (action != octa::AutoSenseAction::finish && action != octa::AutoSenseAction::cancel) {
    require(!stopped && stopped.error().code == -EINVAL && io.writes == writes);
  } else {
    require(stopped.has_value());
  }
}

void exercise_session() {
  auto session = octa::MidiSession::open("hw:fuzz");
  require(session.has_value());
  if (!session) { return; }
  require(session->set_input_meters(true).has_value());
  exercise_auto_sense(*session);
  const auto received = session->receive(std::chrono::seconds(1));
  if (received) { check_message(*received); }
  const auto reply = session->read_bulk_state(&check_message);
  if (reply) {
    require(reply->command == octa::Command::data && reply->address == octa::bulk_address &&
            reply->payload.size() == octa::bulk_size);
    check_message(*reply);
    const auto valid_bulk = octa::validate_bulk_write(*reply);
    const auto writes = io.writes;
    const auto bulk_written = session->write_bulk_state(*reply, &check_message);
    if (!valid_bulk) {
      require(!bulk_written && bulk_written.error().code == -EINVAL && writes == io.writes);
    } else if (bulk_written) {
      require(*bulk_written == *reply);
    }
  }
  const octa::RouteChange change = {.pair = io.bytes.empty() ? std::uint8_t{0} : io.bytes.front(),
                                    .selection =
                                        io.bytes.empty() ? std::uint8_t{0} : io.bytes.back()};
  const auto valid = octa::encode_dsp_change(change);
  const auto previous_writes = io.writes;
  const auto written = session->write_dsp(change, &check_message);
  if (!valid) {
    require(!written && written.error().code == -EINVAL && io.writes == previous_writes);
  } else if (written) {
    const auto state = octa::decode_dsp(*written);
    require(state.has_value());
    if (!state) { return; }
    const auto matches = octa::matches_dsp_change(*state, change);
    require(matches && *matches);
  }
  require(session->set_input_meters(false).has_value());
}

}

// Test-only ALSA symbols: no physical port, poll or file descriptor is used.
// The input stream is replayed when a complete request is sent. An empty input
// fails readiness immediately, so malformed inputs never wait for a timeout.
extern "C" int snd_rawmidi_open(snd_rawmidi_t **in_rmidi,
                                snd_rawmidi_t **out_rmidi,
                                const char * /*name*/,
                                int /*mode*/) {
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
  if (io.position == io.bytes.size()) { return -EAGAIN; }
  const auto count = std::min({size, io.read_size, io.bytes.size() - io.position});
  std::ranges::copy(io.bytes.subspan(io.position, count), static_cast<std::uint8_t *>(buffer));
  io.position += count;
  return static_cast<ssize_t>(count);
}

extern "C" ssize_t
snd_rawmidi_write(snd_rawmidi_t * /*rmidi*/, const void *buffer, std::size_t size) {
  ++io.writes;
  const auto count = std::min(size, io.write_size);
  for (const auto byte : std::span(static_cast<const std::uint8_t *>(buffer), count)) {
    const auto decoded = io.requests.feed(byte);
    require(decoded.has_value());
    if (!decoded) { return -EIO; }
    const auto &message = *decoded;
    if (message.has_value() && message->command == octa::Command::request) { io.position = 0; }
  }
  return static_cast<ssize_t>(count);
}

extern "C" int snd_rawmidi_poll_descriptors_count(snd_rawmidi_t * /*rmidi*/) {
  return 0;
}

extern "C" int snd_rawmidi_poll_descriptors(snd_rawmidi_t * /*rmidi*/,
                                            struct pollfd * /*pfds*/,
                                            unsigned int /*space*/) {
  return -EIO;
}

extern "C" int snd_rawmidi_poll_descriptors_revents(snd_rawmidi_t * /*rawmidi*/,
                                                    struct pollfd * /*pfds*/,
                                                    unsigned int /*nfds*/,
                                                    unsigned short * /*revent*/) {
  return -EIO;
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  io = {};
  io.bytes = std::span(data, size);
  constexpr std::uint8_t chunk_mask = 15;
  io.read_size = size == 0 ? 1
                           : (static_cast<std::size_t>(data[0]) * 2) +
                                 static_cast<std::size_t>(data[size - 1] & 1U) + 1;
  io.write_size = size == 0 ? 1 : static_cast<std::size_t>(data[size - 1] & chunk_mask) + 1;
  exercise_session();
  require(io.closes == 2);
  {
    auto session = octa::MidiSession::open("hw:fake");
    require(session.has_value());
    if (!session) { return 0; }
    const auto action = static_cast<octa::MemoryAction>(size == 0 ? 0 : data[0]);
    const auto writes = io.writes;
    const auto result = session->operate_memory(action, &check_message);
    if (action != octa::MemoryAction::store && action != octa::MemoryAction::load) {
      require(!result && result.error().code == -EINVAL && io.writes == writes);
    } else if (result) {
      require(result->command == octa::Command::data && result->address == octa::bulk_address &&
              result->payload.size() == octa::bulk_size);
    }
  }
  require(io.closes == 4);
  return 0;
}
