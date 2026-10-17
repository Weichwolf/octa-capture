// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PresetState.hpp"

#include "octa/Preset.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iostream>
#include <span>
#include <string_view>
#include <vector>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

std::vector<std::uint8_t> read_fixture(const std::filesystem::path &path, std::size_t size) {
  std::ifstream input(path, std::ios::binary);
  std::vector<std::uint8_t> bytes(size + 1);
  input.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  if (input.bad() || input.gcount() != static_cast<std::streamsize>(size)) { return {}; }
  bytes.resize(size);
  return bytes;
}

bool reference_export(const std::filesystem::path &directory) {
  constexpr std::size_t frame_size = 1159;
  const auto frame = read_fixture(directory / "preset-export-bulk.syx", frame_size);
  const auto expected = read_fixture(directory / "preset-export-reference.octa", octa::preset_size);
  const auto message = octa::decode(frame);
  if (!require(message && !expected.empty(), "load synthetic bulk and original Mac output")) {
    return false;
  }
  const auto preset = octa::preset_from_bulk(*message);
  if (!require(preset && octa::encode_preset(*preset) == expected,
               "bulk export matches original Mac constructor/SetBlk/SaveCpl byte-for-byte")) {
    return false;
  }
  // The reference disk format omits mono, system flags and the unknown master flag.
  constexpr std::array<std::size_t, 3> omitted = {0x60, 0, 0x442};
  for (const auto offset : omitted) {
    auto changed = *message;
    changed.payload[offset] ^= 1;
    const auto exported = octa::preset_from_bulk(changed);
    if (!require(exported && *exported == *preset,
                 "fields omitted from Ver100 do not leak into export")) {
      return false;
    }
  }
  return true;
}

bool raw_ranges() {
  octa::Message message = {.command = octa::Command::data,
                           .address = octa::bulk_address,
                           .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  constexpr std::size_t pan_offset = 0x80;
  constexpr std::size_t volume_offset = 0x120;
  constexpr std::size_t sensitivity_offset = 0x1d;
  constexpr std::size_t type_offset = 0x0a;
  constexpr std::uint8_t maximum_midi = 127;
  for (auto &byte : std::span(message.payload).subspan(pan_offset, 4)) { byte = 15; }
  for (auto &byte : std::span(message.payload).subspan(volume_offset, 6)) { byte = 15; }
  message.payload[sensitivity_offset] = maximum_midi;
  message.payload[type_offset] = maximum_midi;
  const auto preset = octa::preset_from_bulk(message);
  constexpr std::uint32_t maximum_pan = 0xffff;
  constexpr std::uint32_t maximum_volume = 0xffffff;
  return require(
      preset && preset->mixers.front().input.front().channels.front().pan == maximum_pan &&
          preset->mixers.front().input.front().channels.front().volume == maximum_volume &&
          preset->preamps.front().channels.front().sensitivity_index == maximum_midi &&
          preset->reverb.type == maximum_midi,
      "export preserves incoming raw values without applying narrower GUI write limits");
}

bool invalid_states() {
  octa::Message message = {.command = octa::Command::request,
                           .address = octa::bulk_address,
                           .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  const auto command = octa::preset_from_bulk(message);
  message.command = octa::Command::data;
  message.address.front() = 0;
  const auto address = octa::preset_from_bulk(message);
  message.address = octa::bulk_address;
  constexpr std::size_t pan_offset = 0x80;
  message.payload[pan_offset] = 16;
  const auto nibble = octa::preset_from_bulk(message);
  message.payload[pan_offset] = 0;
  constexpr std::uint8_t invalid_midi = 128;
  message.payload.front() = invalid_midi;
  const auto data = octa::preset_from_bulk(message);
  message.payload.front() = 0;
  message.payload.pop_back();
  const auto size = octa::preset_from_bulk(message);
  return require(!command && command.error() == octa::PresetStateError::message && !address &&
                     address.error() == octa::PresetStateError::message && !nibble &&
                     nibble.error() == octa::PresetStateError::data && !data &&
                     data.error() == octa::PresetStateError::data && !size &&
                     size.error() == octa::PresetStateError::data,
                 "invalid bulk envelopes, sizes, MIDI data and packed coefficients are rejected");
}

}

int main(int argc, char **argv) {
  return argc == 2 && reference_export(argv[1]) && raw_ranges() && invalid_states() ? 0 : 1;
}
