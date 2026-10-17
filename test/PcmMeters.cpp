// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeters.hpp"

#include "PcmMeterPacket.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <sstream>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool valid_packets() {
  constexpr std::array<std::uint32_t, 4> rates = {44100, 48000, 96000, 192000};
  for (const auto rate : rates) {
    auto packet = fixture::packet(rate);
    fixture::put(packet, fixture::capture_sequence, std::numeric_limits<std::uint32_t>::max());
    const auto decoded = octa::decode_pcm_meters(packet);
    if (!require(decoded && decoded->capture.rate == rate && decoded->playback.rate == rate &&
                     decoded->capture.peaks[0] == octa::pcm_meter_peak_max &&
                     decoded->capture.fresh && decoded->playback.fresh && decoded->idle_stream &&
                     decoded->capture.channels == (rate == rates.back() ? 4 : 12) &&
                     decoded->playback.channels == (rate == rates.back() ? 4 : 10),
                 "all four rates preserve exact raw peaks and channel layouts")) {
      return false;
    }
    std::ostringstream json;
    octa::write_pcm_meters_json(json, *decoded);
    if (!require(json.str().contains(R"("peaks":[2147483392,0)") &&
                     json.str().contains(R"("fresh":true)"),
                 "JSON preserves peak integers")) {
      return false;
    }
  }
  fixture::Packet empty = {'O', 'C', 'P', 'K', 1, 0, octa::pcm_meter_bytes, 0};
  fixture::put(empty, fixture::capture_age, std::numeric_limits<std::uint32_t>::max());
  fixture::put(empty, fixture::playback_age, std::numeric_limits<std::uint32_t>::max());
  const auto unseen = octa::decode_pcm_meters(empty);
  return require(unseen && !unseen->enabled && !unseen->capture.fresh && !unseen->playback.fresh,
                 "never-seen streams stay explicitly unavailable");
}

bool invalid_packets() {
  constexpr std::uint32_t rate = 48000;
  const auto packet = fixture::packet(rate);
  for (std::size_t size = 0; size < packet.size(); ++size) {
    if (!require(!octa::decode_pcm_meters(std::span(packet).first(size)), "reject truncation")) {
      return false;
    }
  }
  constexpr std::array<std::size_t, 5> headers = {0, 4, 5, 6, 7};
  for (const auto index : headers) {
    auto changed = packet;
    changed[index] ^= 1;
    if (!require(!octa::decode_pcm_meters(changed), "reject magic/version/size")) { return false; }
  }
  constexpr std::uint32_t high_rate = 192000;
  constexpr std::uint32_t impossible_error = 4096;
  constexpr std::uint32_t unknown_flags = 16;
  constexpr std::uint32_t stale_age = octa::pcm_meter_stale_ms + 1;
  constexpr std::array<std::array<std::uint32_t, 2>, 8> mutations = {
      {{fixture::flags, unknown_flags},
       {fixture::error, impossible_error},
       {fixture::capture_rate, 1},
       {fixture::playback_channels, 12},
       {fixture::capture_age, stale_age},
       {fixture::capture_peak, 1},
       {fixture::playback_peak, 0x80000000},
       {fixture::capture_rate, high_rate}}};
  for (const auto &mutation : mutations) {
    auto changed = packet;
    fixture::put(changed, mutation[0], mutation[1]);
    if (!require(!octa::decode_pcm_meters(changed), "reject inconsistent stream/peak fields")) {
      return false;
    }
  }
  auto changed = fixture::packet(high_rate);
  fixture::put(changed, fixture::capture_peak + (4 * sizeof(std::uint32_t)), 256);
  return require(!octa::decode_pcm_meters(changed), "unused channels must be zero");
}

bool freshness_boundaries() {
  constexpr std::uint32_t rate = 48000;
  auto packet = fixture::packet(rate);
  fixture::put(packet, fixture::capture_age, octa::pcm_meter_stale_ms);
  fixture::put(packet, fixture::playback_age, octa::pcm_meter_stale_ms);
  if (!require(octa::decode_pcm_meters(packet).has_value(), "last fresh millisecond")) {
    return false;
  }
  fixture::put(packet, fixture::flags, 3);
  fixture::put(packet, fixture::capture_age, octa::pcm_meter_stale_ms + 1);
  fixture::put(packet, fixture::playback_age, octa::pcm_meter_stale_ms + 1);
  const auto stale = octa::decode_pcm_meters(packet);
  return require(stale && !stale->capture.fresh && !stale->playback.fresh,
                 "expired data remains inspectable and explicitly stale");
}

}

int main() {
  return valid_packets() && invalid_packets() && freshness_boundaries() ? 0 : 1;
}
