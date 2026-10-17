// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DirectOut.hpp"

#include "octa/DspControl.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
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

std::vector<std::uint8_t> read(const std::filesystem::path &path, std::size_t expected) {
  std::ifstream input(path, std::ios::binary);
  std::vector<std::uint8_t> bytes(expected + 1);
  input.read(reinterpret_cast<char *>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
  if (input.bad() || input.gcount() != static_cast<std::streamsize>(expected)) { return {}; }
  bytes.resize(expected);
  return bytes;
}

std::vector<std::uint8_t> device_reference(const std::vector<std::uint8_t> &cached_frame) {
  auto cached = octa::decode(cached_frame);
  if (!cached) { return {}; }
  constexpr std::size_t io_link = 0x43e;
  constexpr std::size_t playback_volume = 0x44a;
  constexpr std::size_t digits = 6;
  constexpr std::array<std::uint8_t, digits> unity = {2, 0, 0, 0, 0, 0};
  for (std::size_t bus = 0; bus < 4; ++bus) {
    if ((cached->payload[io_link + bus] & 1U) == 0) { continue; }
    for (std::size_t side = 0; side < 2; ++side) {
      const auto destination =
          std::span(cached->payload)
              .subspan(playback_volume + (((bus * 2) + side) * digits), digits);
      std::ranges::copy(unity, destination.begin());
    }
  }
  const auto wire = octa::encode(cached->command, cached->address, cached->payload);
  return wire ? *wire : std::vector<std::uint8_t>{};
}

bool reference(const std::filesystem::path &directory) {
  constexpr std::size_t bulk_frame_size = octa::bulk_size + 13;
  constexpr std::size_t trace_size = 3294;
  const auto current =
      octa::decode(read(directory / "preset-import-reference.syx", bulk_frame_size));
  if (!current) { return false; }
  const auto target = octa::direct_out_target(*current);
  if (!require(target.has_value(), "valid Direct Out current state")) { return false; }
  const auto wire = octa::encode(target->command, target->address, target->payload);
  const auto expected =
      device_reference(read(directory / "direct-out-state-reference.syx", bulk_frame_size));
  if (!require(
          wire && !expected.empty() && *wire == expected,
          "original DirectOut/TxBlk cache with independently observed firmware master coupling")) {
    return false;
  }
  const auto changes = octa::direct_out_changes();
  std::vector<std::uint8_t> trace;
  for (const auto &change : changes) {
    const auto frame = octa::encode_dsp_change(change);
    if (!require(frame.has_value(), "all Direct Out controls satisfy typed write bounds")) {
      return false;
    }
    trace.insert(trace.end(), frame->begin(), frame->end());
  }
  const auto reference_trace = read(directory / "direct-out-reference.syx", trace_size);
  return require(changes.size() == octa::direct_out_change_count && !reference_trace.empty() &&
                     trace == reference_trace,
                 "all 206 frames match original DirectOut exactly and in order");
}

bool preservation() {
  octa::Message current{.command = octa::Command::data,
                        .address = octa::bulk_address,
                        .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  constexpr std::size_t preamp_offset = 0x15;
  constexpr std::size_t preamp_size = 0x43;
  constexpr std::size_t mono = 0x60;
  constexpr std::size_t opaque = 0x442;
  constexpr std::uint8_t all_bits = 127;
  current.payload[mono] = all_bits;
  current.payload[opaque] = all_bits;
  current.payload[9] = 8;
  const auto target = octa::direct_out_target(current);
  if (!target) { return false; }
  const auto repeated = octa::direct_out_target(*target);
  const auto valid = octa::validate_bulk_write(*target);
  return require(
      valid && repeated && *repeated == *target && target->payload[mono] == all_bits &&
          target->payload[opaque] == all_bits && target->payload[9] == 8 &&
          std::ranges::equal(std::span(current.payload).subspan(preamp_offset, preamp_size),
                             std::span(target->payload).subspan(preamp_offset, preamp_size)),
      "Direct Out is idempotent and preserves mono, opaque bits, preamps and coaxial route");
}

bool master_coupling() {
  octa::Message current{.command = octa::Command::data,
                        .address = octa::bulk_address,
                        .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  constexpr std::size_t io_link = 0x43e;
  constexpr std::size_t playback_volume = 0x44a;
  constexpr std::size_t digits = 6;
  constexpr std::array<std::uint8_t, digits> unity = {2, 0, 0, 0, 0, 0};
  for (std::size_t bus = 0; bus < 4; ++bus) {
    current.payload[io_link + bus] = static_cast<std::uint8_t>(bus % 2);
    current.payload[playback_volume + (bus * 12) + 5] = 1;
    current.payload[playback_volume + (bus * 12) + 11] = 2;
  }
  const auto target = octa::direct_out_target(current);
  if (!target) { return false; }
  for (std::size_t bus = 0; bus < 4; ++bus) {
    for (std::size_t side = 0; side < 2; ++side) {
      const auto offset = playback_volume + (((bus * 2) + side) * digits);
      const auto actual = std::span(target->payload).subspan(offset, digits);
      const auto expected =
          bus % 2 == 1 ? std::span<const std::uint8_t>(unity)
                       : std::span<const std::uint8_t>(current.payload).subspan(offset, digits);
      if (!require(
              std::ranges::equal(actual, expected),
              "active I/O master links propagate unity; unlinked playback masters preserved")) {
        return false;
      }
    }
  }
  return true;
}

bool rejection() {
  octa::Message current{.command = octa::Command::data,
                        .address = octa::bulk_address,
                        .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  current.payload[2] = 3;
  const auto rate = octa::direct_out_target(current);
  if (!require(!rate && rate.error() == octa::DirectOutError::rate,
               "192 kHz Direct Out rejected")) {
    return false;
  }
  current.payload[2] = 0;
  current.payload.pop_back();
  const auto size = octa::direct_out_target(current);
  return require(!size && size.error() == octa::DirectOutError::message, "truncated bulk rejected");
}

}

int main(int argc, char **argv) {
  if (argc != 2) { return 2; }
  return reference(argv[1]) && preservation() && master_coupling() && rejection() ? 0 : 1;
}
