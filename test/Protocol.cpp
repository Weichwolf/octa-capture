// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <iostream>
#include <span>
#include <string_view>
#include <vector>

namespace {

constexpr std::array<std::uint8_t, 17> bulk_request = {
    0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x11, 1, 0, 0, 0, 0, 0, 8, 0x7a, 0x7d, 0xf7};
constexpr std::array<std::uint8_t, 14> sensitivity_frame = {
    0xf0, 0x41, 0x10, 0, 0, 0x4d, 0x12, 0, 5, 0, 4, 0x14, 0x63, 0xf7};
constexpr std::array<std::uint8_t, 4> bulk_size_bytes = {0, 0, 8, 0x7a};
constexpr std::size_t original_bulk_frame_size = 1159;
constexpr std::uint32_t largest_request = 0x0fffffff;

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool reference_frames() {
  const auto request = octa::request(octa::bulk_address, octa::bulk_size);
  if (!require(request && std::ranges::equal(*request, bulk_request), "original bulk request")) {
    return false;
  }
  const std::array<std::uint8_t, 1> value = {0x14};
  const auto update = octa::encode(octa::Command::data, {0, 5, 0, 4}, value);
  if (!require(update && std::ranges::equal(*update, sensitivity_frame),
               "original sensitivity frame")) {
    return false;
  }
  const auto decoded = octa::decode(bulk_request);
  return require(decoded && decoded->command == octa::Command::request &&
                     decoded->address == octa::bulk_address &&
                     std::ranges::equal(decoded->payload, bulk_size_bytes),
                 "decode original bulk request");
}

bool corrupted_frames() {
  for (std::size_t position = 0; position < sensitivity_frame.size(); ++position) {
    auto corrupted = sensitivity_frame;
    corrupted[position] ^= 1;
    if (!require(!octa::decode(corrupted), "single-bit corruption must be rejected")) {
      return false;
    }
  }
  for (std::size_t length = 0; length < sensitivity_frame.size(); ++length) {
    if (!require(!octa::decode(std::span(sensitivity_frame).first(length)), "truncation")) {
      return false;
    }
  }
  auto status_byte = sensitivity_frame;
  status_byte[11] = 0x80;
  return require(!octa::decode(status_byte), "embedded MIDI status byte");
}

bool limits() {
  const std::array<std::uint8_t, 1> value = {0};
  const std::array<std::uint8_t, 1> invalid_data = {0xff};
  const std::array<std::uint8_t, 4> empty_request{};
  const std::vector<std::uint8_t> oversized(octa::maximum_payload + 1);
  return require(!octa::encode(octa::Command::data, {}, {}), "empty data") &&
         require(!octa::encode(octa::Command::data, {}, oversized), "oversized data") &&
         require(!octa::encode(octa::Command::data, {0x80, 0, 0, 0}, value), "invalid address") &&
         require(!octa::encode(octa::Command::data, {}, invalid_data), "invalid data") &&
         require(!octa::encode(octa::Command::request, {}, empty_request), "empty request size") &&
         require(!octa::request({}, 0), "zero-length request") &&
         require(!octa::request({}, 0x10000000), "request size exceeds four MIDI bytes");
}

bool bulk_response() {
  std::vector<std::uint8_t> payload(octa::bulk_size);
  for (std::size_t i = 0; i < payload.size(); ++i) {
    payload[i] = static_cast<std::uint8_t>(i % 128);
  }
  const auto frame = octa::encode(octa::Command::data, octa::bulk_address, payload);
  if (!require(frame && frame->size() == original_bulk_frame_size,
               "original bulk response length")) {
    return false;
  }
  const auto message = octa::decode(*frame);
  return require(message && message->payload == payload && message->address == octa::bulk_address,
                 "bulk payload preserves every byte");
}

bool request_boundaries() {
  const auto frame = octa::request({0x7f, 0x7f, 0x7f, 0x7f}, largest_request);
  if (!require(frame.has_value(), "maximum request size")) { return false; }
  const auto message = octa::decode(*frame);
  const std::vector<std::uint8_t> size = {0x7f, 0x7f, 0x7f, 0x7f};
  return require(message && message->payload == size, "base-128 request size");
}

}

int main() {
  const bool passed = reference_frames() && corrupted_frames() && limits() && bulk_response() &&
                      request_boundaries();
  if (passed) { std::cout << "Protocol reference vectors and malformed-frame checks passed.\n"; }
  return passed ? 0 : 1;
}
