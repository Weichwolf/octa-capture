// SPDX-License-Identifier: GPL-2.0-only
#include "pcm-peaks.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  const std::span bytes(data, size);
  if (bytes.empty()) { return 0; }
  octa_pcm_peak_values peaks{};
  const auto channels = bytes.front();
  const auto samples = bytes.subspan(1);
  const auto before = peaks;
  const bool accepted = octa_pcm_scan_peaks24(&peaks, samples.data(), samples.size(), channels);
  const bool valid =
      channels != 0 && channels <= OCTA_PCM_PEAK_CHANNELS &&
      samples.size() % (static_cast<std::size_t>(channels) * OCTA_PCM_SAMPLE_BYTES) == 0;
  if (accepted != valid || (!accepted && !std::ranges::equal(peaks.values, before.values))) {
    __builtin_trap();
  }
  for (const auto peak : peaks.values) {
    constexpr unsigned int maximum = 0x7fffff00;
    constexpr unsigned int byte_mask = 0xff;
    if (peak > maximum || (peak & byte_mask) != 0) { __builtin_trap(); }
  }
  return 0;
}
