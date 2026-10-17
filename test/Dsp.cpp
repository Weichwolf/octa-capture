// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"

#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <utility>
#include <vector>

namespace {

constexpr std::uint32_t first_input_pan = 0x1234;
constexpr std::uint32_t last_input_pan = 0xabcd;
constexpr std::uint32_t first_playback_pan = 0x4567;
constexpr std::uint32_t last_playback_pan = 0x789a;
constexpr std::uint32_t first_input_volume = 0x123456;
constexpr std::uint32_t last_input_volume = 0x654321;
constexpr std::uint32_t first_playback_volume = 0x112233;
constexpr std::uint32_t last_playback_volume = 0xccbbaa;
constexpr std::uint32_t first_input_master = 0x123abc;
constexpr std::uint32_t right_input_master = 0x456def;
constexpr std::uint32_t last_input_master = 0xdef456;
constexpr std::uint32_t first_playback_master = 0x234abc;
constexpr std::uint32_t last_playback_master = 0xabc987;
constexpr std::uint32_t first_send = 0x010203;
constexpr std::uint32_t last_send = 0xa0b0c0;
constexpr std::uint32_t maximum_pan = 0xffff;
constexpr std::uint32_t maximum_volume = 0xffffff;
constexpr std::size_t last_input_pan_offset = 0x11c;
constexpr std::size_t first_playback_pan_offset = 0x274;
constexpr std::size_t last_playback_pan_offset = 0x310;
constexpr std::size_t input_link_offset = 0x58;
constexpr std::size_t input_master_link_offset = 0x404;
constexpr std::size_t input_volume_offset = 0x120;
constexpr std::uint32_t reserved_flags = 0x71;
constexpr std::uint32_t maximum_index = 127;
constexpr std::uint32_t first_time_index = 21;
constexpr std::uint32_t second_time_index = 22;
constexpr std::uint32_t third_time_index = 23;
constexpr std::uint32_t fourth_time_index = 24;
constexpr std::uint32_t fifth_time_index = 25;

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

template <std::size_t width>
void number(octa::Message &message, std::size_t offset, std::uint32_t value) {
  constexpr unsigned int nibble_bits = 4;
  constexpr std::uint32_t nibble_mask = 15;
  for (std::size_t digit = width; digit > 0; --digit) {
    message.payload[offset + digit - 1] = static_cast<std::uint8_t>(value & nibble_mask);
    value >>= nibble_bits;
  }
}

octa::Message fixture() {
  octa::Message message = {.command = octa::Command::data,
                           .address = octa::bulk_address,
                           .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  // Independent boundary values at the first and last field of each column.
  constexpr std::array<std::pair<std::size_t, std::uint32_t>, 12> volumes = {
      {{input_volume_offset, first_input_volume},
       {0x20a, last_input_volume},
       {0x246, last_send},
       {0x314, first_playback_volume},
       {0x3fe, last_playback_volume},
       {0x408, first_input_master},
       {0x432, last_input_master},
       {0x438, first_input_volume},
       {0x44a, first_playback_master},
       {0x474, last_playback_master},
       {0x210, first_send},
       {0x40e, right_input_master}}};
  for (const auto &[offset, value] : volumes) { number<6>(message, offset, value); }
  number<4>(message, 0x80, first_input_pan);
  number<4>(message, last_input_pan_offset, last_input_pan);
  number<4>(message, first_playback_pan_offset, first_playback_pan);
  number<4>(message, last_playback_pan_offset, last_playback_pan);
  constexpr std::array<std::size_t, 13> bit_zero = {input_link_offset,
                                                    0x5f,
                                                    0x67,
                                                    0x74,
                                                    0x24c,
                                                    0x253,
                                                    0x25b,
                                                    input_master_link_offset,
                                                    0x407,
                                                    0x441,
                                                    0x446,
                                                    0x449,
                                                    0x268};
  for (const auto offset : bit_zero) { message.payload[offset] = 1; }
  constexpr std::array<std::size_t, 4> bit_one = {0x73, 0x7f, 0x267, 0x273};
  for (const auto offset : bit_one) { message.payload[offset] = 2; }
  for (std::uint8_t index = 0; index < 5; ++index) {
    message.payload[5 + index] = static_cast<std::uint8_t>(index == 0 ? 0 : index + 4);
    message.payload[0x0b + index] = static_cast<std::uint8_t>(1 + index);
    message.payload[0x10 + index] = static_cast<std::uint8_t>(first_time_index + index);
  }
  message.payload[0x0a] = 4;
  return message;
}

bool channels() {
  const auto state = octa::decode_dsp(fixture());
  if (!require(state.has_value(), "decode DSP bulk layout")) { return false; }
  const auto &first = state->mixers.front();
  const auto &last = state->mixers.back();
  return require(first.input.channels.front() == octa::MixerChannel{.mute = true,
                                                                    .solo = false,
                                                                    .pan = first_input_pan,
                                                                    .volume = first_input_volume} &&
                     last.input.channels.back() ==
                         octa::MixerChannel{.mute = true,
                                            .solo = true,
                                            .pan = last_input_pan,
                                            .volume = last_input_volume} &&
                     first.playback.channels.front() ==
                         octa::MixerChannel{.mute = true,
                                            .solo = false,
                                            .pan = first_playback_pan,
                                            .volume = first_playback_volume} &&
                     last.playback.channels.back() ==
                         octa::MixerChannel{.mute = true,
                                            .solo = true,
                                            .pan = last_playback_pan,
                                            .volume = last_playback_volume},
                 "independent input/playback channel columns and last bus") &&
         require(first.input.pairs.front() == octa::MixerPair{.linked = true, .mono = false} &&
                     last.input.pairs.back() == octa::MixerPair{.linked = true, .mono = true} &&
                     first.playback.pairs.front() ==
                         octa::MixerPair{.linked = true, .mono = false} &&
                     last.playback.pairs.back() == octa::MixerPair{.linked = true, .mono = true} &&
                     !state->mixers[1].input.pairs[1].linked,
                 "packed stereo pairs span two bytes per bus");
}

bool masters_and_effects() {
  const auto state = octa::decode_dsp(fixture());
  if (!state) { return false; }
  const auto &first = state->mixers.front();
  const auto &last = state->mixers.back();
  const auto &reverb = state->reverb;
  return require(first.input_master ==
                         octa::MixerMaster{.linked = true,
                                           .volume = {first_input_master, right_input_master}} &&
                     last.input_master.linked && last.input_master.volume[1] == last_input_master &&
                     first.playback_master.linked &&
                     first.playback_master.volume[0] == first_playback_master &&
                     last.playback_master.linked &&
                     last.playback_master.volume[1] == last_playback_master &&
                     !first.input_output_link && last.input_output_link,
                 "master columns and input/output links") &&
         require(reverb.type == 4 &&
                     reverb.pre_delay_indices == std::array<std::uint8_t, 5>{1, 2, 3, 4, 5} &&
                     reverb.time_indices == std::array<std::uint8_t, 5>{first_time_index,
                                                                        second_time_index,
                                                                        third_time_index,
                                                                        fourth_time_index,
                                                                        fifth_time_index} &&
                     reverb.input_send.front() == first_send &&
                     reverb.input_send.back() == last_send &&
                     reverb.master_return == first_input_volume &&
                     state->output_routes == std::array<std::uint8_t, 5>{0, 5, 6, 7, 8},
                 "reverb banks, sends, return and output routes");
}

bool malformed() {
  auto message = fixture();
  for (std::size_t size = 0; size < octa::bulk_size; ++size) {
    message.payload.resize(size);
    if (!require(!octa::decode_dsp(message), "reject every truncated bulk size")) { return false; }
  }
  message = fixture();
  message.payload.push_back(0);
  if (!require(!octa::decode_dsp(message), "reject oversized bulk")) { return false; }
  message = fixture();
  message.command = octa::Command::request;
  if (!require(!octa::decode_dsp(message), "reject request")) { return false; }
  message = fixture();
  message.address[0] = 0;
  if (!require(!octa::decode_dsp(message), "reject another address")) { return false; }
  message = fixture();
  message.payload[1] = 0x80;
  if (!require(!octa::decode_dsp(message), "reject non-MIDI data outside known DSP fields")) {
    return false;
  }
  constexpr std::array<std::size_t, 8> edges = {
      0x80, 0x24b, first_playback_pan_offset, 0x403, 0x408, 0x43d, 0x44a, 0x479};
  for (const auto offset : edges) {
    message = fixture();
    message.payload[offset] = 0x10;
    if (!require(!octa::decode_dsp(message), "reject non-nibble coefficients at region edges")) {
      return false;
    }
  }
  return true;
}

bool wire_values() {
  auto message = fixture();
  message.payload[input_link_offset] = reserved_flags;
  message.payload[input_master_link_offset] = reserved_flags;
  message.payload[0x0a] = maximum_index;
  number<4>(message, 0x80, maximum_pan);
  number<6>(message, input_volume_offset, maximum_volume);
  const auto state = octa::decode_dsp(message);
  return require(state && state->mixers[0].input.pairs[0].linked &&
                     !state->mixers[0].input.pairs[1].linked &&
                     state->mixers[0].input_master.linked &&
                     state->mixers[0].input.channels[0].pan == maximum_pan &&
                     state->mixers[0].input.channels[0].volume == maximum_volume &&
                     state->reverb.type == maximum_index,
                 "ignore reserved flag bits and retain incoming wire indices and coefficients");
}

}

int main() {
  return channels() && masters_and_effects() && malformed() && wire_values() ? 0 : 1;
}
