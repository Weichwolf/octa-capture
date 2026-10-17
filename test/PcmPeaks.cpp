// SPDX-License-Identifier: GPL-2.0-only
#include "pcm-peaks.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <span>
#include <string_view>
#include <vector>

namespace {

constexpr unsigned int byte_mask = 0xff;
constexpr unsigned int sample_mask = 0xffffff;
constexpr unsigned int positive_maximum = 0x7fffff;
constexpr unsigned int positive_peak_maximum = 0x7fffff00;
constexpr unsigned int negative_minimum = 0x800000;
constexpr unsigned int sign_shift = 8;

bool require(bool condition, std::string_view message) {
  if (!condition) { std::cerr << "FAIL: " << message << '\n'; }
  return condition;
}

void append_sample(std::vector<std::uint8_t> &data, unsigned int value) {
  for (unsigned int byte = 0; byte < OCTA_PCM_SAMPLE_BYTES; ++byte) {
    data.push_back(static_cast<std::uint8_t>((value >> (byte * sign_shift)) & byte_mask));
  }
}

std::vector<std::uint8_t> packet(unsigned int channels) {
  constexpr std::array samples = {0U, 1U, positive_maximum, negative_minimum, sample_mask};
  std::vector<std::uint8_t> data;
  for (std::size_t frame = 0; frame < samples.size(); ++frame) {
    for (unsigned int channel = 0; channel < channels; ++channel) {
      const auto value = samples[(frame + channel) % samples.size()];
      append_sample(data, value);
    }
  }
  return data;
}

bool boundaries() {
  const std::array<unsigned int, 7> inputs = {
      0, 1, positive_maximum, negative_minimum, negative_minimum + 1, sample_mask - 1, sample_mask};
  const std::array<unsigned int, 7> expected = {0, 256, positive_peak_maximum, 0, 0, 0, 0};
  for (std::size_t index = 0; index < inputs.size(); ++index) {
    std::vector<std::uint8_t> bytes;
    append_sample(bytes, inputs[index]);
    if (!require(octa_pcm_positive_peak24(bytes.data()) == expected[index],
                 "positive-only peak with defined unsigned shifts")) {
      return false;
    }
  }
  return true;
}

bool accumulation() {
  for (unsigned int channels = 1; channels <= OCTA_PCM_PEAK_CHANNELS; ++channels) {
    octa_pcm_peak_values peaks{};
    const auto data = packet(channels);
    if (!require(octa_pcm_scan_peaks24(&peaks, data.data(), data.size(), channels),
                 "valid complete interleaved frames accepted")) {
      return false;
    }
    for (unsigned int channel = 0; channel < OCTA_PCM_PEAK_CHANNELS; ++channel) {
      const auto expected = channel < channels ? positive_peak_maximum : 0;
      if (!require(peaks.values[channel] == expected, "per-channel maximum and unused storage")) {
        return false;
      }
    }
    const auto previous = peaks;
    std::vector<std::uint8_t> lower(static_cast<std::size_t>(channels) * OCTA_PCM_SAMPLE_BYTES, 0);
    if (!require(octa_pcm_scan_peaks24(&peaks, lower.data(), lower.size(), channels) &&
                     std::ranges::equal(peaks.values, previous.values),
                 "later lower peaks do not erase accumulated maxima")) {
      return false;
    }
  }
  return true;
}

bool malformed() {
  const std::array<std::uint8_t, 7> bytes{};
  octa_pcm_peak_values peaks{};
  std::ranges::fill(peaks.values, 256U);
  const auto previous = peaks;
  for (const auto channels : {0U, 13U, std::numeric_limits<unsigned int>::max()}) {
    if (!require(!octa_pcm_scan_peaks24(&peaks, bytes.data(), bytes.size(), channels),
                 "invalid channel counts rejected")) {
      return false;
    }
  }
  for (const auto size : {1U, 2U, 4U, 7U}) {
    if (!require(!octa_pcm_scan_peaks24(&peaks, bytes.data(), size, 2),
                 "partial packets rejected before mutation")) {
      return false;
    }
  }
  return require(!octa_pcm_scan_peaks24(nullptr, bytes.data(), bytes.size(), 2) &&
                     !octa_pcm_scan_peaks24(&peaks, nullptr, 0, 2) &&
                     octa_pcm_scan_peaks24(&peaks, bytes.data(), 0, 2) &&
                     std::ranges::equal(peaks.values, previous.values),
                 "null and empty buffers are bounded and invalid input preserves all peaks");
}

void reference_case(unsigned int channels, std::span<const std::uint8_t> bytes) {
  octa_pcm_peak_values peaks{};
  if (!octa_pcm_scan_peaks24(&peaks, bytes.data(), bytes.size(), channels)) { __builtin_trap(); }
  std::cout << R"({"channels":)" << channels << R"(,"bytes":")";
  for (const auto byte : bytes) {
    std::cout << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned int>(byte);
  }
  std::cout << std::dec << R"(","peaks":[)";
  for (unsigned int channel = 0; channel < channels; ++channel) {
    if (channel != 0) { std::cout << ','; }
    std::cout << peaks.values[channel];
  }
  std::cout << "]}";
}

void reference_cases() {
  constexpr std::array samples = {0U, 1U, 2U, positive_maximum, negative_minimum, sample_mask};
  std::cout << '[';
  bool first = true;
  for (const auto channels : {4U, 10U, 12U}) {
    for (const auto sample : samples) {
      std::vector<std::uint8_t> bytes;
      for (unsigned int channel = 0; channel < channels; ++channel) {
        append_sample(bytes, sample);
      }
      if (!first) { std::cout << ','; }
      first = false;
      reference_case(channels, bytes);
    }
    std::vector<std::uint8_t> independent;
    for (unsigned int channel = 0; channel < channels; ++channel) {
      append_sample(independent, channel + 1);
    }
    std::cout << ',';
    reference_case(channels, independent);
    std::cout << ',';
    reference_case(channels, packet(channels));
  }
  std::cout << "]\n";
}

}

int main(int argc, char **argv) {
  static_assert(sizeof(unsigned int) == sizeof(std::uint32_t));
  if (argc == 2 && std::string_view(argv[1]) == "--reference") {
    reference_cases();
    return std::cout.good() ? 0 : 1;
  }
  return boundaries() && accumulation() && malformed() ? 0 : 1;
}
