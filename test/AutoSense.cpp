// SPDX-License-Identifier: GPL-2.0-only
#include "octa/AutoSense.hpp"

#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool reference_frames() {
  constexpr std::uint8_t selected = 0x91;
  constexpr std::array<std::uint8_t, 15> start = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 2, 1, 4, 1, 9, 0x6f, 0xf7};
  constexpr std::array<std::uint8_t, 14> finish = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 2, 1, 5, 0, 0x78, 0xf7};
  constexpr std::array<std::uint8_t, 14> cancel = {
      0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 2, 1, 5, 1, 0x77, 0xf7};
  const auto started = octa::encode_auto_sense_start(selected);
  const auto finished = octa::encode_auto_sense_stop(octa::AutoSenseAction::finish);
  const auto cancelled = octa::encode_auto_sense_stop(octa::AutoSenseAction::cancel);
  return require(started && std::ranges::equal(*started, start),
                 "selected inputs use low nibble first") &&
         require(finished && std::ranges::equal(*finished, finish),
                 "finish operation and checksum") &&
         require(cancelled && std::ranges::equal(*cancelled, cancel),
                 "cancel operation and checksum");
}

bool notifications() {
  octa::AutoSenseChannels channels{};
  const octa::Message selection = {
      .command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {1, 9}};
  constexpr octa::AutoSenseChannels expected = {
      true, false, false, false, true, false, false, true};
  const auto changed = octa::apply_auto_sense_update(channels, selection);
  if (!require(changed && *changed && channels == expected,
               "selection notification in input order")) {
    return false;
  }
  auto reserved = selection;
  constexpr std::uint8_t reserved_bits = 0x70;
  for (auto &byte : reserved.payload) { byte |= reserved_bits; }
  const auto unchanged = octa::apply_auto_sense_update(channels, reserved);
  if (!require(unchanged && !*unchanged && channels == expected, "ignore reserved flag bits")) {
    return false;
  }
  const octa::Message completion = {
      .command = octa::Command::data, .address = {0, 2, 1, 5}, .payload = {0}};
  const auto cleared = octa::apply_auto_sense_update(channels, completion);
  const auto repeated = octa::apply_auto_sense_update(channels, completion);
  return require(cleared && *cleared && repeated && !*repeated &&
                     channels == octa::AutoSenseChannels{},
                 "completion clears activity and repeated completion is idempotent");
}

bool all_input_masks() {
  octa::AutoSenseChannels channels{};
  constexpr unsigned int maximum_mask = 255;
  for (unsigned int mask = 1; mask <= maximum_mask; ++mask) {
    const auto frame = octa::encode_auto_sense_start(static_cast<std::uint8_t>(mask));
    if (!frame) { return false; }
    const auto message = octa::decode(*frame);
    if (!message) { return false; }
    const auto changed = octa::apply_auto_sense_update(channels, *message);
    if (!changed) { return false; }
    for (unsigned int channel = 0; channel < channels.size(); ++channel) {
      if (!require(channels[channel] == ((mask & (1U << channel)) != 0),
                   "all nonempty masks preserve channel identity through the wire codec")) {
        return false;
      }
    }
  }
  return true;
}

bool malformed() {
  octa::AutoSenseChannels channels{};
  channels.front() = true;
  const auto original = channels;
  const std::array<octa::Message, 5> invalid = {
      {{.command = octa::Command::request, .address = {0, 2, 1, 4}, .payload = {1, 0}},
       {.command = octa::Command::data, .address = {0, 2, 2, 4}, .payload = {1, 0}},
       {.command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {1}},
       {.command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {1, 0x80}},
       {.command = octa::Command::data, .address = {0, 2, 1, 5}, .payload = {0, 0}}}};
  for (const auto &message : invalid) {
    if (!require(!octa::apply_auto_sense_update(channels, message) && channels == original,
                 "invalid notifications leave the complete activity bank untouched")) {
      return false;
    }
  }
  const auto action = std::bit_cast<octa::AutoSenseAction>(std::uint8_t{2});
  return require(!octa::encode_auto_sense_start(0),
                 "empty selection cannot start automatic gain") &&
         require(!octa::encode_auto_sense_stop(action), "reject invalid stop action");
}

}

int main() {
  return reference_frames() && notifications() && all_input_masks() && malformed() ? 0 : 1;
}
