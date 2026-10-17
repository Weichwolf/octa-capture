// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <vector>

namespace {

constexpr std::uint8_t bus_d_channel_10 = 0x39;
constexpr std::uint8_t bus_c_channel_9 = 0x28;
constexpr std::uint8_t bus_b_channel_6 = 0x15;
constexpr std::uint8_t bus_d_channel_9 = 0x38;
constexpr std::uint8_t bus_b_channel_7 = 0x16;
constexpr std::uint8_t bus_d_right = 0x31;
constexpr std::uint8_t bus_d_left = 0x30;

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool update(octa::DspState &state,
            const octa::DspState &expected,
            octa::Address address,
            const std::vector<std::uint8_t> &payload) {
  const auto wire = octa::encode(octa::Command::data, address, payload);
  if (!wire) { return false; }
  const auto message = octa::decode(*wire);
  if (!message) { return false; }
  const bool different = state != expected;
  const auto changed = octa::apply_dsp_update(state, *message);
  if (!require(changed && *changed == different && state == expected,
               "notification modifies only the independently selected field")) {
    return false;
  }
  const auto repeated = octa::apply_dsp_update(state, *message);
  return require(repeated && !*repeated && state == expected,
                 "repeated notification is idempotent");
}

bool mixer_channels() {
  octa::DspState state{};
  auto expected = state;
  constexpr std::uint16_t right_pan = 0x8000;
  constexpr std::uint32_t unity = 0x200000;
  expected.mixers[3].input.channels[9].pan = right_pan;
  if (!update(state, expected, {0, 6, bus_d_channel_10, 4}, {8, 0, 0, 0})) { return false; }
  expected.mixers[0].playback.channels[0].volume = unity;
  if (!update(state, expected, {0, 7, 0, 8}, {2, 0, 0, 0, 0, 0})) { return false; }
  expected.mixers[2].playback.channels[8].mute = true;
  if (!update(state, expected, {0, 7, bus_c_channel_9, 3}, {1})) { return false; }
  expected.mixers[1].input.channels[5].solo = true;
  if (!update(state, expected, {0, 6, bus_b_channel_6, 2}, {2})) { return false; }
  expected.mixers[3].input.pairs[4].linked = true;
  if (!update(state, expected, {0, 6, bus_d_channel_9, 0}, {1})) { return false; }
  // The two channel addresses alias the same stereo pair in incoming messages.
  if (!update(state, expected, {0, 6, bus_d_channel_10, 0}, {1})) { return false; }
  expected.mixers[1].playback.pairs[3].mono = true;
  return update(state, expected, {0, 7, bus_b_channel_7, 1}, {1});
}

bool mixer_masters() {
  octa::DspState state{};
  auto expected = state;
  constexpr std::uint32_t coefficient = 0x123456;
  expected.mixers[0].input_master.volume[1] = coefficient;
  if (!update(state, expected, {0, 8, 1, 1}, {1, 2, 3, 4, 5, 6})) { return false; }
  expected.mixers[3].playback_master.volume[1] = coefficient;
  if (!update(state, expected, {0, 9, bus_d_right, 1}, {1, 2, 3, 4, 5, 6})) { return false; }
  expected.mixers[2].input_master.linked = true;
  if (!update(state, expected, {0, 8, 0x20, 0}, {1})) { return false; }
  expected.mixers[1].playback_master.linked = true;
  if (!update(state, expected, {0, 9, 0x10, 0}, {1})) { return false; }
  expected.mixers[3].input_output_link = true;
  return update(state, expected, {0, 8, bus_d_left, 0x0d}, {1});
}

bool effects_and_routes() {
  octa::DspState state{};
  auto expected = state;
  constexpr std::uint32_t coefficient = 0xabcdef;
  constexpr std::uint8_t time_index = 44;
  expected.reverb.input_send[9] = coefficient;
  if (!update(state, expected, {0, 6, 9, 0x0e}, {10, 11, 12, 13, 14, 15})) { return false; }
  expected.reverb.master_return = coefficient;
  if (!update(state, expected, {0, 8, 0, 7}, {10, 11, 12, 13, 14, 15})) { return false; }
  expected.reverb.type = 4;
  if (!update(state, expected, {0, 4, 0, 0}, {4})) { return false; }
  expected.reverb.pre_delay_indices[1] = 11;
  if (!update(state, expected, {0, 4, 1, 1}, {11})) { return false; }
  expected.reverb.time_indices[4] = time_index;
  if (!update(state, expected, {0, 4, 4, 2}, {time_index})) { return false; }
  expected.output_routes[4] = 8;
  return update(state, expected, {0, 3, 0, 4}, {8});
}

bool invalid_addresses() {
  octa::DspState state{};
  state.mixers[1].input.channels[1].pan = 1;
  const auto original = state;
  constexpr std::array<octa::Address, 18> addresses = {{{1, 6, 0, 0},
                                                        {0, 2, 0, 0},
                                                        {0, 6, 0x40, 0},
                                                        {0, 6, 0x0a, 0},
                                                        {0, 7, 0x3f, 0},
                                                        {0, 6, 0, 5},
                                                        {0, 7, 0, 0x0e},
                                                        {0, 6, 0x10, 0x0e},
                                                        {0, 8, 2, 1},
                                                        {0, 9, 0x40, 1},
                                                        {0, 8, 0x10, 7},
                                                        {0, 9, 0, 7},
                                                        {0, 9, 0, 0x0d},
                                                        {0, 8, 0, 0x0e},
                                                        {0, 4, 5, 1},
                                                        {0, 4, 1, 0},
                                                        {0, 3, 1, 0},
                                                        {0, 3, 0, 5}}};
  for (const auto address : addresses) {
    const octa::Message message = {
        .command = octa::Command::data, .address = address, .payload = {1}};
    if (!require(!octa::apply_dsp_update(state, message) && state == original,
                 "unknown groups, parameters and out-of-range selectors preserve state")) {
      return false;
    }
  }
  return true;
}

bool payload_lengths() {
  struct Case {
    octa::Address address;
    std::size_t width;
  };

  constexpr std::array<Case, 4> cases = {{{.address = {0, 6, 0, 0}, .width = 1},
                                          {.address = {0, 3, 0, 0}, .width = 1},
                                          {.address = {0, 6, 0, 4}, .width = 4},
                                          {.address = {0, 9, 1, 1}, .width = 6}}};
  octa::DspState state{};
  const auto original = state;
  for (const auto &test : cases) {
    for (std::size_t length = 0; length <= 7; ++length) {
      const octa::Message message = {.command = octa::Command::data,
                                     .address = test.address,
                                     .payload = std::vector<std::uint8_t>(length)};
      const auto result = octa::apply_dsp_update(state, message);
      if (!require(result.has_value() == (length == test.width) && state == original,
                   "all scalar and coefficient targets require their exact payload length")) {
        return false;
      }
    }
  }
  return true;
}

bool invalid_data() {
  octa::DspState state{};
  state.reverb.type = 1;
  const auto original = state;
  const std::array<std::vector<std::uint8_t>, 5> payloads = {
      {{}, {0, 0, 0}, {0, 0, 0, 0, 0}, {0, 0, 16, 0}, {0x80, 0, 0, 0}}};
  for (const auto &payload : payloads) {
    const octa::Message message = {
        .command = octa::Command::data, .address = {0, 6, 0, 4}, .payload = payload};
    if (!require(!octa::apply_dsp_update(state, message) && state == original,
                 "invalid length, MIDI data and coefficient digits preserve state")) {
      return false;
    }
  }
  octa::Message message = {
      .command = octa::Command::request, .address = {0, 4, 0, 0}, .payload = {2}};
  if (!require(!octa::apply_dsp_update(state, message) && state == original,
               "RQ1 is not a state notification")) {
    return false;
  }
  message.command = octa::Command::data;
  message.address[2] = 0x80;
  return require(!octa::apply_dsp_update(state, message) && state == original,
                 "reject non-MIDI address bytes");
}

}

int main() {
  return mixer_channels() && mixer_masters() && effects_and_routes() && invalid_addresses() &&
                 payload_lengths() && invalid_data()
             ? 0
             : 1;
}
