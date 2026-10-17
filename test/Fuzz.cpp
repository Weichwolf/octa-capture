// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Meters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <cmath>
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

void check_dsp_control(const octa::DspChange &change) {
  const auto frame = octa::encode_dsp_change(change);
  octa::DspState state{};
  const auto before = octa::matches_dsp_change(state, change);
  require(frame.has_value() == before.has_value());
  if (!frame) { return; }
  const auto message = octa::decode(*frame);
  require(message.has_value());
  if (!message) { return; }
  const auto applied = octa::apply_dsp_update(state, *message);
  require(applied && before && *before != *applied);
  const auto after = octa::matches_dsp_change(state, change);
  require(after && *after);
}

void typed_controls(std::span<const std::uint8_t> bytes) {
  constexpr std::size_t minimum_size = 9;
  if (bytes.size() < minimum_size) { return; }
  std::uint32_t value = 0;
  constexpr unsigned int byte_bits = 8;
  for (const auto byte : bytes.subspan(5, 4)) { value = (value << byte_bits) | byte; }
  const auto source = static_cast<octa::MixerSource>(bytes[1]);
  check_dsp_control(octa::MixerChange{.source = source,
                                      .bus = bytes[2],
                                      .channel = bytes[3],
                                      .parameter = static_cast<octa::MixerParameter>(bytes[4]),
                                      .value = value});
  check_dsp_control(octa::MasterChange{.source = source,
                                       .bus = bytes[2],
                                       .channel = bytes[3],
                                       .parameter = static_cast<octa::MasterParameter>(bytes[4]),
                                       .value = value});
  check_dsp_control(octa::ReverbChange{.parameter = static_cast<octa::ReverbParameter>(bytes[1]),
                                       .bank = bytes[2],
                                       .value = bytes[3]});
  check_dsp_control(octa::RouteChange{.pair = bytes[1], .selection = bytes[2]});
}

void meter_packets(std::span<const std::uint8_t> bytes) {
  octa::MeterReceiver receiver;
  for (const auto byte : bytes) {
    const auto result = receiver.feed(byte);
    if (!result) { continue; }
    const auto &packet = *result;
    if (packet.has_value()) {
      for (const auto value : *packet) {
        const auto decibels = octa::input_meter_db(value);
        require(decibels.has_value());
      }
    }
  }
  constexpr std::size_t meter_bytes = 16;
  std::vector<std::uint8_t> payload(meter_bytes);
  const auto count = std::min(bytes.size(), payload.size());
  std::ranges::transform(bytes.first(count), payload.begin(), [](std::uint8_t byte) {
    constexpr std::uint8_t data_mask = 127;
    return static_cast<std::uint8_t>(byte & data_mask);
  });
  const octa::Message message = {
      .command = octa::Command::data, .address = {0, 10, 0, 1}, .payload = payload};
  const auto meters = octa::decode_input_meters(message);
  require(meters.has_value());
  if (!meters) { return; }
  for (const auto value : *meters) {
    const auto decibels = octa::input_meter_db(value);
    constexpr double silence = -96.0;
    require(decibels && std::isfinite(*decibels) && *decibels >= silence && *decibels <= 0.0);
  }
  const auto frame = octa::encode(message.command, message.address, message.payload);
  require(frame.has_value());
  if (!frame) { return; }
  bool recovered = false;
  for (const auto byte : *frame) {
    const auto result = receiver.feed(byte);
    require(result.has_value());
    if (!result) { return; }
    const auto &packet = *result;
    if (packet.has_value()) { recovered = *packet == *meters; }
  }
  require(recovered);
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
  const auto meters = octa::decode_input_meters(message);
  if (meters) { round_trip(message); }
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
  typed_controls(bytes);
  meter_packets(bytes);
  return 0;
}
