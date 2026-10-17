// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PcmMeters.hpp"

#include <cstddef>
#include <cstdint>
#include <span>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  const auto meters = octa::decode_pcm_meters(std::span(data, size));
  if (!meters) { return 0; }
  if (size != octa::pcm_meter_bytes) { __builtin_trap(); }
  for (const auto *direction : {&meters->capture, &meters->playback}) {
    if (direction->channels > direction->peaks.size() ||
        (direction->fresh && direction->age_ms > octa::pcm_meter_stale_ms)) {
      __builtin_trap();
    }
    for (std::size_t channel = 0; channel < direction->peaks.size(); ++channel) {
      const auto peak = direction->peaks[channel];
      constexpr std::uint32_t alignment = 256;
      if (peak > octa::pcm_meter_peak_max || peak % alignment != 0 ||
          (channel >= direction->channels && peak != 0)) {
        __builtin_trap();
      }
    }
  }
  return 0;
}
