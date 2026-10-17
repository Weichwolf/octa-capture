// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Meters.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool reference_levels() {
  const octa::Message message = {
      .command = octa::Command::data,
      .address = {0, 10, 0, 1},
      .payload = {0, 0, 0, 1, 0, 127, 1, 0, 1, 1, 64, 0, 127, 126, 127, 127}};
  constexpr octa::InputMeters expected = {0, 1, 127, 128, 129, 8192, 16382, 16383};
  const auto decoded = octa::decode_input_meters(message);
  return require(decoded && *decoded == expected, "eight independent big-endian base-128 levels");
}

bool switch_frames() {
  constexpr std::array<std::uint8_t, 14> start = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 10, 0, 0, 1, 0x75, 0xf7};
  constexpr std::array<std::uint8_t, 14> stop = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 10, 0, 0, 0, 0x76, 0xf7};
  const auto enabled = octa::encode_meter_switch(true);
  const auto disabled = octa::encode_meter_switch(false);
  return require(enabled && enabled->size() == start.size() &&
                     std::equal(enabled->begin(), enabled->end(), start.begin()) && disabled &&
                     disabled->size() == stop.size() &&
                     std::equal(disabled->begin(), disabled->end(), stop.begin()),
                 "meter switch frames and Roland checksums");
}

bool captured_silence() {
  // The connected device emitted this complete frame while inputs were silent.
  constexpr std::array<std::uint8_t, 29> frame = {0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0,    10,  0,
                                                  1,    0,    0,    0, 0, 0,    0,    0,    0,   0,
                                                  0,    0,    0,    0, 0, 0,    0,    0x75, 0xf7};
  const auto message = octa::decode(frame);
  if (!require(message.has_value(), "captured meter envelope and checksum")) { return false; }
  const auto meters = octa::decode_input_meters(*message);
  return require(meters && *meters == octa::InputMeters{}, "captured silent input bank");
}

bool malformed() {
  octa::Message message = {.command = octa::Command::data, .address = {0, 10, 0, 1}, .payload = {}};
  constexpr std::size_t expected_size = 16;
  for (std::size_t size = 0; size <= expected_size + 1; ++size) {
    message.payload.resize(size);
    const auto decoded = octa::decode_input_meters(message);
    if (!require(decoded.has_value() == (size == expected_size), "exact payload length")) {
      return false;
    }
  }
  message.payload.resize(expected_size);
  message.payload.back() = 0x80;
  if (!require(!octa::decode_input_meters(message), "reject non-MIDI level bytes")) {
    return false;
  }
  message.payload.back() = 0;
  message.address.back() = 0;
  if (!require(!octa::decode_input_meters(message), "switch echo is not a level packet")) {
    return false;
  }
  message.address.back() = 1;
  message.command = octa::Command::request;
  return require(!octa::decode_input_meters(message), "request is not a level packet");
}

bool streaming() {
  const auto control = octa::encode_meter_switch(true);
  constexpr std::array<std::uint8_t, 16> levels = {0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0, 7, 0, 8};
  const auto frame = octa::encode(octa::Command::data, {0, 10, 0, 1}, levels);
  if (!control || !frame) { return false; }
  octa::MeterReceiver receiver;
  for (const auto byte : *control) {
    const auto result = receiver.feed(byte);
    if (!require(result && !result->has_value(), "ignore a valid non-meter notification")) {
      return false;
    }
  }
  bool received = false;
  for (const auto byte : *frame) {
    constexpr std::uint8_t clock = 0xf8;
    const auto realtime = receiver.feed(clock);
    if (!require(realtime && !realtime->has_value(), "interleaved realtime byte")) { return false; }
    const auto result = receiver.feed(byte);
    if (!require(result.has_value(), "fragmented level packet")) { return false; }
    const auto &packet = *result;
    if (packet.has_value()) {
      constexpr octa::InputMeters expected = {1, 2, 3, 4, 5, 6, 7, 8};
      if (!require(!received && *packet == expected, "one complete bank in channel order")) {
        return false;
      }
      received = true;
    }
  }
  return require(received, "one complete fragmented meter bank");
}

bool stream_recovery() {
  constexpr std::array<std::uint8_t, 16> levels{};
  const auto frame = octa::encode(octa::Command::data, {0, 10, 0, 1}, levels);
  if (!frame) { return false; }
  octa::MeterReceiver receiver;
  const auto short_frame =
      octa::encode(octa::Command::data, {0, 10, 0, 1}, std::span(levels).first(levels.size() - 1));
  if (!short_frame) { return false; }
  bool rejected = false;
  for (const auto byte : *short_frame) {
    const auto result = receiver.feed(byte);
    if (!result) { rejected = result.error() == octa::ProtocolError::size; }
  }
  bool recovered = false;
  for (const auto byte : *frame) {
    const auto result = receiver.feed(byte);
    if (!result) { return false; }
    recovered = recovered || result->has_value();
  }
  return require(rejected && recovered, "reject malformed known packets and recover");
}

bool decibels() {
  constexpr std::uint16_t full_scale = 16383;
  constexpr std::uint16_t midpoint = 8192;
  constexpr double expected_midpoint = -6.020069735;
  constexpr double tolerance = 0.000001;
  constexpr double silence = -96.0;
  const auto zero = octa::input_meter_db(0);
  const auto full = octa::input_meter_db(full_scale);
  const auto half = octa::input_meter_db(midpoint);
  return require(zero && *zero == silence && full && *full == 0.0 && half &&
                     std::abs(*half - expected_midpoint) < tolerance,
                 "reference logarithmic conversion and zero sentinel") &&
         require(!octa::input_meter_db(full_scale + 1), "reject levels outside 14-bit range");
}

}

int main() {
  return reference_levels() && switch_frames() && captured_silence() && malformed() &&
                 streaming() && stream_recovery() && decibels()
             ? 0
             : 1;
}
