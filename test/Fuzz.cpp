// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace {

void require(bool condition) {
  if (!condition) { __builtin_trap(); }
}

void round_trip(const octa::Message &message) {
  const auto encoded = octa::encode(message.command, message.address, message.payload);
  require(encoded.has_value());
  if (!encoded) { return; }
  const auto decoded = octa::decode(*encoded);
  require(decoded && *decoded == message);
}

void check_preamp(octa::PreampBank &bank, const octa::Message &message) {
  const auto previous = bank;
  const auto changed = octa::apply_preamp_update(bank, message);
  if (!changed) {
    require(bank == previous);
    return;
  }
  require(*changed == (bank != previous));
  const auto repeated = octa::apply_preamp_update(bank, message);
  require(repeated && !*repeated);
}

void check_dsp(octa::DspState &state, const octa::Message &message) {
  const auto previous = state;
  const auto changed = octa::apply_dsp_update(state, message);
  if (!changed) {
    require(state == previous);
    return;
  }
  require(*changed == (state != previous));
  const auto repeated = octa::apply_dsp_update(state, message);
  require(repeated && !*repeated);
}

void frames(std::span<const std::uint8_t> bytes) {
  const auto decoded = octa::decode(bytes);
  if (decoded) {
    round_trip(*decoded);
    const auto dsp = octa::decode_dsp(*decoded);
    if (dsp) { require(decoded->address == octa::bulk_address); }
  }
  octa::SysExReceiver receiver;
  octa::PreampBank bank{};
  octa::DspState dsp_state{};
  for (const auto byte : bytes) {
    const auto result = receiver.feed(byte);
    if (!result) { continue; }
    const auto &message = *result;
    if (message.has_value()) {
      round_trip(*message);
      const auto preamps = octa::decode_preamps(*message);
      if (preamps) { bank = *preamps; }
      check_preamp(bank, *message);
      check_dsp(dsp_state, *message);
    }
  }
  // Every malformed or unfinished stream must recover at the next SysEx start.
  const auto recovery = octa::request(octa::bulk_address, octa::bulk_size);
  require(recovery.has_value());
  if (!recovery) { return; }
  for (const auto byte : *recovery) {
    const auto result = receiver.feed(byte);
    require(result.has_value());
    if (!result) { return; }
    const auto &message = *result;
    if (byte == recovery->back()) {
      require(message.has_value());
      if (message.has_value()) { require(message->command == octa::Command::request); }
    } else {
      require(!message.has_value());
    }
  }
}

void structured(std::span<const std::uint8_t> bytes) {
  constexpr std::size_t prefix_size = 5;
  if (bytes.size() < prefix_size) { return; }
  octa::Address address{};
  std::ranges::copy(bytes.subspan(1, address.size()), address.begin());
  const auto payload = bytes.subspan(prefix_size);
  const octa::Message message = {.command = static_cast<octa::Command>(bytes.front()),
                                 .address = address,
                                 .payload = {payload.begin(), payload.end()}};
  const auto encoded = octa::encode(message.command, message.address, message.payload);
  if (encoded) {
    const auto decoded = octa::decode(*encoded);
    require(decoded && *decoded == message);
  }
  octa::PreampBank bank{};
  octa::DspState dsp_state{};
  check_preamp(bank, message);
  check_dsp(dsp_state, message);
  const octa::PreampChange change = {.channel = address[2],
                                     .parameter = static_cast<octa::PreampParameter>(address[3]),
                                     .value = bytes.front()};
  const auto write = octa::encode_preamp_change(change);
  if (write) {
    const auto notification = octa::decode(*write);
    require(notification.has_value());
    if (notification) {
      check_preamp(bank, *notification);
      const auto observed = octa::preamp_value(bank, change.channel, change.parameter);
      require(observed && *observed == change.value);
    }
  }
  const auto dsp = octa::decode_dsp(message);
  if (dsp) { round_trip(message); }
  const auto preamps = octa::decode_preamps(message);
  if (preamps) { round_trip(message); }

  // Reach packed flags and parameter columns without needing a valid wire checksum.
  std::vector<std::uint8_t> bulk(octa::bulk_size);
  const auto count = std::min(bulk.size(), bytes.size());
  std::ranges::transform(bytes.first(count), bulk.begin(), [](std::uint8_t byte) {
    constexpr std::uint8_t data_mask = 0x7f;
    return static_cast<std::uint8_t>(byte & data_mask);
  });
  octa::Message synthetic = {
      .command = octa::Command::data, .address = octa::bulk_address, .payload = bulk};
  const auto decoded = octa::decode_preamps(synthetic);
  require(decoded.has_value());
  if (decoded) {
    bank = *decoded;
    check_preamp(bank, message);
  }
  for (auto &byte : synthetic.payload) {
    constexpr std::uint8_t nibble_mask = 15;
    byte &= nibble_mask;
  }
  const auto synthetic_dsp = octa::decode_dsp(synthetic);
  require(synthetic_dsp.has_value());
  if (synthetic_dsp) {
    dsp_state = *synthetic_dsp;
    check_dsp(dsp_state, message);
  }
}

}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  const std::span bytes(data, size);
  frames(bytes);
  structured(bytes);
  return 0;
}
