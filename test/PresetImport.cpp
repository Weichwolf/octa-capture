// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DspControl.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
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

octa::Message current_state() {
  return {.command = octa::Command::data,
          .address = octa::bulk_address,
          .payload = std::vector<std::uint8_t>(octa::bulk_size)};
}

std::vector<std::uint8_t> read_fixture(const std::filesystem::path &path, std::size_t size) {
  std::ifstream input(path, std::ios::binary);
  std::vector<std::uint8_t> bytes(size + 1);
  input.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  if (input.bad() || input.gcount() != static_cast<std::streamsize>(size)) { return {}; }
  bytes.resize(size);
  return bytes;
}

bool reference(const std::filesystem::path &directory) {
  constexpr std::size_t frame_size = 1159;
  const auto bulk = read_fixture(directory / "preset-export-bulk.syx", frame_size);
  const auto profile = read_fixture(directory / "preset-import-profile.octa", octa::preset_size);
  const auto expected = read_fixture(directory / "preset-import-reference.syx", frame_size);
  const auto current = octa::decode(bulk);
  const auto preset = octa::decode_preset(profile);
  if (!require(current && preset && !expected.empty(), "load independent import fixtures")) {
    return false;
  }
  const auto target = octa::preset_to_bulk(*preset, *current);
  if (!require(target.has_value(), "valid reference preset prepares successfully")) {
    return false;
  }
  const auto wire = octa::encode(target->command, target->address, target->payload);
  return require(
      wire && *wire == expected,
      "prepared message matches original LoadCpl/Set/TxBlk, including resets and checksum");
}

bool rejects(const octa::Preset &preset) {
  const auto result = octa::preset_to_bulk(preset, current_state());
  return require(!result && result.error() == octa::PresetStateError::range,
                 "out-of-bound effective preset value rejected without returning a partial target");
}

bool preamp_bounds() {
  constexpr std::array<std::uint32_t, 7> maxima = {100, 50, 25, 45, 50, 8, 74};
  constexpr std::uint32_t maximum_word = 0xffffffff;
  for (std::size_t pair = 0; pair < 4; ++pair) {
    for (std::size_t side = 0; side < 2; ++side) {
      for (std::size_t index = 0; index < maxima.size(); ++index) {
        for (const auto value : {maxima[index] + 1, maximum_word}) {
          octa::Preset preset{};
          auto &channel = preset.preamps[pair].channels[side];
          if (index == 0) {
            channel.sensitivity_index = value;
          } else {
            channel.compressor.indices[index - 1] = value;
          }
          if (!rejects(preset)) { return false; }
        }
      }
    }
  }
  return true;
}

bool channel_bounds(std::size_t bus, octa::MixerSource source, std::size_t index) {
  constexpr std::uint32_t invalid_pan = 0x8001;
  constexpr std::uint32_t invalid_volume = 0x800000;
  octa::Preset preset{};
  auto &group =
      source == octa::MixerSource::input ? preset.mixers[bus].input : preset.mixers[bus].playback;
  auto &channel = group[index / 2].channels[index % 2];
  channel.pan = invalid_pan;
  if (!rejects(preset)) { return false; }
  channel.pan = 0;
  channel.volume = invalid_volume;
  if (!rejects(preset)) { return false; }
  channel.volume = 0;
  if (bus == 0 && source == octa::MixerSource::input) {
    channel.reverb_send = invalid_volume;
    if (!rejects(preset)) { return false; }
  }
  return true;
}

bool mixer_bounds() {
  for (std::size_t bus = 0; bus < 4; ++bus) {
    for (const auto source : {octa::MixerSource::input, octa::MixerSource::playback}) {
      for (std::size_t channel = 0; channel < 10; ++channel) {
        if (!channel_bounds(bus, source, channel)) { return false; }
      }
    }
  }
  return true;
}

bool master_bounds() {
  constexpr std::uint32_t invalid_volume = 0x800000;
  for (std::size_t bus = 0; bus < 4; ++bus) {
    for (std::size_t source = 0; source < 2; ++source) {
      for (std::size_t side = 0; side < 2; ++side) {
        octa::Preset preset{};
        auto &master =
            source == 0 ? preset.mixers[bus].input_master : preset.mixers[bus].playback_master;
        master.volume[side] = invalid_volume;
        if (!rejects(preset)) { return false; }
      }
    }
  }
  octa::Preset preset{};
  preset.mixers.front().input_master.reverb_return = invalid_volume;
  return rejects(preset);
}

bool global_bounds() {
  constexpr std::uint32_t invalid_midi = 128;
  for (std::size_t bank = 0; bank < 5; ++bank) {
    octa::Preset preset{};
    constexpr std::uint32_t invalid_delay = 13;
    constexpr std::uint32_t invalid_time = 50;
    preset.reverb.pre_delay_indices[bank] = bank == 0 ? invalid_midi : invalid_delay;
    if (!rejects(preset)) { return false; }
    preset.reverb.pre_delay_indices[bank] = 0;
    preset.reverb.time_indices[bank] = bank == 0 ? invalid_midi : invalid_time;
    if (!rejects(preset)) { return false; }
  }
  for (std::size_t pair = 0; pair < 5; ++pair) {
    octa::Preset preset{};
    preset.output_routes[pair] = 9;
    if (!rejects(preset)) { return false; }
  }
  octa::Preset preset{};
  preset.reverb.type = 5;
  return rejects(preset);
}

bool baseline_validation() {
  auto current = current_state();
  current.command = octa::Command::request;
  const auto command = octa::preset_to_bulk({}, current);
  current.command = octa::Command::data;
  current.payload.pop_back();
  const auto size = octa::preset_to_bulk({}, current);
  return require(!command && command.error() == octa::PresetStateError::message && !size &&
                     size.error() == octa::PresetStateError::data,
                 "current state must be a valid bulk DT1 before a target is prepared");
}

}

int main(int argc, char **argv) {
  return argc == 2 && reference(argv[1]) && preamp_bounds() && mixer_bounds() && master_bounds() &&
                 global_bounds() && baseline_validation()
             ? 0
             : 1;
}
