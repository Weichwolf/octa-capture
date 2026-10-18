// SPDX-License-Identifier: GPL-2.0-only
#include "octa/AutoSense.hpp"
#include "octa/DeviceStatus.hpp"
#include "octa/DirectOut.hpp"
#include "octa/Dsp.hpp"
#include "octa/DspControl.hpp"
#include "octa/Initialization.hpp"
#include "octa/InputMonitor.hpp"
#include "octa/Meters.hpp"
#include "octa/PcmMeters.hpp"
#include "octa/Preamp.hpp"
#include "octa/Preset.hpp"
#include "octa/PresetState.hpp"
#include "octa/Protocol.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

namespace {

void require(bool condition) {
  if (!condition) { __builtin_trap(); }
}

void device_status(std::span<const std::uint8_t> bytes) {
  const auto status = octa::decode_device_status(bytes);
  if (!status) { return; }
  require(bytes.size() == octa::device_status_bytes && bytes[0] == 1);
  require(std::ranges::equal(bytes, status->raw));
  require(status->sample_rate_hz != 0);
  require(status->clock_active == (bytes[7] != 0));
  require((status->clock_source == octa::ClockSource::external) == ((bytes[6] & 1U) != 0));
}

void pcm_status(std::span<const std::uint8_t> bytes) {
  const auto meters = octa::decode_pcm_meters(bytes);
  if (!meters) { return; }
  require(bytes.size() == octa::pcm_meter_bytes);
  for (const auto *direction : {&meters->capture, &meters->playback}) {
    require(direction->channels <= direction->peaks.size());
    require(!direction->fresh || direction->age_ms <= octa::pcm_meter_stale_ms);
    for (const auto peak : direction->peaks) { require(peak <= octa::pcm_meter_peak_max); }
  }
}

void round_trip(const octa::Message &message) {
  const auto encoded = octa::encode(message.command, message.address, message.payload);
  require(encoded.has_value());
  if (!encoded) { return; }
  const auto decoded = octa::decode(*encoded);
  require(decoded && *decoded == message);
}

std::uint32_t nibble_value(std::span<const std::uint8_t> bytes) {
  std::uint32_t value = 0;
  constexpr unsigned int nibble_bits = 4;
  constexpr std::uint8_t nibble_limit = 16;
  for (const auto byte : bytes) {
    require(byte < nibble_limit);
    value = (value << nibble_bits) | byte;
  }
  return value;
}

void input_monitor_target(std::span<const std::uint8_t> bytes, octa::InputMonitorChange change) {
  octa::Message before = {.command = octa::Command::data,
                          .address = octa::bulk_address,
                          .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  const auto count = std::min(before.payload.size(), bytes.size());
  std::ranges::transform(bytes.first(count), before.payload.begin(), [](auto byte) {
    constexpr std::uint8_t nibble_mask = 15;
    return static_cast<std::uint8_t>(byte & nibble_mask);
  });
  before.payload[2] &= 3;
  const auto target = octa::input_monitor_target(before, change);
  const auto frame = octa::encode_input_monitor(change);
  const unsigned int active = before.payload[2] == 3 ? 4 : 10;
  require(target.has_value() == (frame && (!change.channel || *change.channel < active)));
  if (!target) { return; }
  require(target->command == before.command && target->address == before.address &&
          target->payload.size() == before.payload.size());
  const auto prior = octa::decode_dsp(before);
  const auto after = octa::decode_dsp(*target);
  require(prior && after);
  if (!prior || !after) { return; }
  auto expected = *prior;
  for (std::size_t channel = 0; channel < 10; ++channel) {
    const bool addressed = !change.channel || channel == *change.channel;
    const bool peer = change.channel && prior->mixers[0].input.pairs[*change.channel / 2].linked &&
                      channel == (*change.channel ^ 1U);
    auto &input = expected.mixers[0].input.channels[channel];
    if (addressed) { input.pan = change.pan; }
    if (addressed || peer) {
      input.mute = !change.enabled;
      input.volume = change.volume;
    }
  }
  require(*after == expected);
  constexpr std::size_t first_input_controls = 0x74;
  require(std::ranges::equal(std::span(before.payload).first(first_input_controls),
                             std::span(target->payload).first(first_input_controls)));
}

void input_monitor(std::span<const std::uint8_t> bytes) {
  constexpr std::size_t required = 8;
  if (bytes.size() < required) { return; }
  std::uint32_t volume = 0;
  constexpr unsigned int byte_bits = 8;
  for (const auto byte : bytes.subspan(4, 4)) { volume = (volume << byte_bits) | byte; }
  const auto pan =
      static_cast<std::uint16_t>((static_cast<unsigned int>(bytes[2]) << byte_bits) | bytes[3]);
  const std::optional<std::uint8_t> channel =
      (bytes[1] & 2U) != 0 ? std::nullopt : std::optional(bytes[0]);
  constexpr std::uint32_t maximum_volume = 0x7fffff;
  const octa::InputMonitorChange change = {
      .channel = channel, .enabled = (bytes[1] & 1U) != 0, .pan = pan, .volume = volume};
  input_monitor_target(bytes, change);
  const auto frame = octa::encode_input_monitor(change);
  require(frame.has_value() ==
          ((!channel || *channel < 12) && pan <= 0x8000 && volume <= maximum_volume));
  if (!frame) { return; }
  const auto message = octa::decode(*frame);
  require(message && message->payload.size() == 11);
  if (message) {
    const std::span payload(message->payload);
    constexpr std::uint8_t broadcast = 0x7f;
    require(message->address[2] == channel.value_or(broadcast));
    require(payload[0] == static_cast<std::uint8_t>((bytes[1] & 1U) == 0));
    require(nibble_value(payload.subspan(1, 4)) == pan);
    require(nibble_value(payload.last(6)) == volume);
    round_trip(*message);
  }
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

void check_auto_sense(octa::AutoSenseChannels &channels, const octa::Message &message) {
  const auto previous = channels;
  const auto changed = octa::apply_auto_sense_update(channels, message);
  if (!changed) {
    require(channels == previous);
    return;
  }
  require(*changed == (channels != previous));
  const auto repeated = octa::apply_auto_sense_update(channels, message);
  require(repeated && !*repeated);
}

void auto_sense_controls(std::span<const std::uint8_t> bytes) {
  if (bytes.empty()) { return; }
  octa::AutoSenseChannels channels{};
  const auto start = octa::encode_auto_sense_start(bytes.front());
  require(start.has_value() == (bytes.front() != 0));
  if (start) {
    const auto message = octa::decode(*start);
    require(message.has_value());
    if (!message) { return; }
    check_auto_sense(channels, *message);
    for (unsigned int channel = 0; channel < channels.size(); ++channel) {
      require(channels[channel] == ((bytes.front() & (1U << channel)) != 0));
    }
  }
  const auto action = static_cast<octa::AutoSenseAction>(bytes.back());
  const auto stop = octa::encode_auto_sense_stop(action);
  require(stop.has_value() == (bytes.back() <= 1));
  if (stop) {
    const auto message = octa::decode(*stop);
    require(message.has_value());
    if (!message) { return; }
    check_auto_sense(channels, *message);
    require(channels == octa::AutoSenseChannels{});
  }
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

void preset_files(std::span<const std::uint8_t> bytes) {
  const auto decoded = octa::decode_preset(bytes);
  if (decoded) {
    const auto encoded = octa::encode_preset(*decoded);
    const auto repeated = octa::decode_preset(encoded);
    require(repeated && *repeated == *decoded);
    if (repeated) { require(octa::encode_preset(*repeated) == encoded); }
  }
  // Preserve the header to exercise every raw DWORD and flag without a valid
  // file signature being a prerequisite for mutation coverage.
  auto synthetic = octa::encode_preset(octa::Preset{});
  constexpr std::size_t header_size = 39;
  const auto count = std::min(bytes.size(), synthetic.size() - header_size);
  std::ranges::copy(bytes.first(count), std::span(synthetic).subspan(header_size).begin());
  const auto state = octa::decode_preset(synthetic);
  require(state.has_value());
  if (!state) { return; }
  const auto encoded = octa::encode_preset(*state);
  const auto repeated = octa::decode_preset(encoded);
  require(repeated && *repeated == *state && encoded.size() == octa::preset_size);
}

void check_direct_out(const octa::Message &message) {
  const auto target = octa::direct_out_target(message);
  if (!target) { return; }
  require(octa::validate_bulk_write(*target).has_value());
  const auto repeated = octa::direct_out_target(*target);
  require(repeated && *repeated == *target);
  require(target->payload[9] == message.payload[9]);
  constexpr std::size_t preamp_offset = 0x15;
  constexpr std::size_t preamp_size = 0x43;
  require(std::ranges::equal(std::span(target->payload).subspan(preamp_offset, preamp_size),
                             std::span(message.payload).subspan(preamp_offset, preamp_size)));
  constexpr std::size_t io_link = 0x43e;
  constexpr std::size_t playback_volume = 0x44a;
  constexpr std::size_t digits = 6;
  constexpr std::array<std::uint8_t, digits> unity = {2, 0, 0, 0, 0, 0};
  for (std::size_t bus = 0; bus < 4; ++bus) {
    for (std::size_t side = 0; side < 2; ++side) {
      const auto offset = playback_volume + (((bus * 2) + side) * digits);
      const auto expected = (message.payload[io_link + bus] & 1U) != 0
                                ? std::span<const std::uint8_t>(unity)
                                : std::span(message.payload).subspan(offset, digits);
      require(std::ranges::equal(std::span(target->payload).subspan(offset, digits), expected));
    }
  }
}

void check_preset_export(const octa::Message &message) {
  check_direct_out(message);
  for (const auto target : {octa::InitializationTarget::all,
                            octa::InitializationTarget::preamps,
                            octa::InitializationTarget::direct_mix,
                            octa::InitializationTarget::reverb,
                            octa::InitializationTarget::patchbay}) {
    const auto matches = octa::initialization_scope_matches(message, message, target);
    if (matches) { require(*matches); }
  }
  const auto exported = octa::preset_from_bulk(message);
  const auto writable = octa::validate_bulk_write(message);
  if (!exported) {
    require(!writable && writable.error() == exported.error());
    return;
  }
  require(writable.has_value() == octa::validate_preset(*exported).has_value());
  require(message.command == octa::Command::data && message.address == octa::bulk_address &&
          message.payload.size() == octa::bulk_size);
  const auto encoded = octa::encode_preset(*exported);
  const auto decoded = octa::decode_preset(encoded);
  require(decoded && *decoded == *exported);
}

void check_preset_import(const octa::Preset &preset, const octa::Message &current) {
  const auto validated = octa::validate_preset(preset);
  const auto target = octa::preset_to_bulk(preset, current);
  require(validated.has_value() == target.has_value());
  if (!target) { return; }
  require(target->command == octa::Command::data && target->address == octa::bulk_address &&
          target->payload.size() == octa::bulk_size);
  const auto wire = octa::encode(target->command, target->address, target->payload);
  require(wire.has_value());
  if (!wire) { return; }
  const auto decoded = octa::decode(*wire);
  require(decoded && *decoded == *target);
  require(target->payload[0] == (current.payload[0] & 1U) &&
          target->payload[1] == current.payload[1] && target->payload[2] == current.payload[2] &&
          target->payload[3] == (current.payload[3] & 1U) &&
          target->payload[4] == (current.payload[2] == 3 ? 0 : current.payload[4]));
  require(octa::validate_bulk_write(*target).has_value());
  const auto exported = octa::preset_from_bulk(*target);
  require(exported.has_value());
  if (!exported) { return; }
  const auto repeated = octa::preset_to_bulk(*exported, *target);
  require(repeated && *repeated == *target);
}

void preset_imports(std::span<const std::uint8_t> bytes) {
  octa::Message current = {.command = octa::Command::data,
                           .address = octa::bulk_address,
                           .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  const auto count = std::min(current.payload.size(), bytes.size());
  std::ranges::transform(bytes.first(count), current.payload.begin(), [](std::uint8_t byte) {
    return static_cast<std::uint8_t>(byte & 15U);
  });
  const auto decoded = octa::decode_preset(bytes);
  if (decoded) { check_preset_import(*decoded, current); }
  auto synthetic = octa::encode_preset(octa::Preset{});
  constexpr std::size_t header_size = 39;
  const auto file_count = std::min(bytes.size(), synthetic.size() - header_size);
  std::ranges::copy(bytes.first(file_count), std::span(synthetic).subspan(header_size).begin());
  const auto arbitrary = octa::decode_preset(synthetic);
  require(arbitrary.has_value());
  if (arbitrary) { check_preset_import(*arbitrary, current); }
  octa::Preset valid{};
  if (!bytes.empty()) {
    valid.reverb.type = bytes.front() % 5U;
    valid.preamps.back().channels.back().phantom = (bytes.front() & 1U) != 0;
    constexpr unsigned int volume_shift = 12;
    valid.mixers.back().playback.back().channels.back().volume =
        static_cast<std::uint32_t>(bytes.back()) << volume_shift;
  }
  const auto target = octa::preset_to_bulk(valid, current);
  require(target.has_value());
  check_preset_import(valid, current);
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
  octa::AutoSenseChannels auto_sense{};
  for (const auto byte : bytes) {
    const auto result = receiver.feed(byte);
    if (!result) { continue; }
    const auto &message = *result;
    if (message.has_value()) {
      round_trip(*message);
      check_preset_export(*message);
      const auto preamps = octa::decode_preamps(*message);
      if (preamps) { bank = *preamps; }
      check_preamp(bank, *message);
      check_dsp(dsp_state, *message);
      check_auto_sense(auto_sense, *message);
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
  check_preset_export(message);
  const auto encoded = octa::encode(message.command, message.address, message.payload);
  if (encoded) {
    const auto decoded = octa::decode(*encoded);
    require(decoded && *decoded == message);
  }
  octa::PreampBank bank{};
  octa::DspState dsp_state{};
  octa::AutoSenseChannels auto_sense{};
  check_preamp(bank, message);
  check_dsp(dsp_state, message);
  check_auto_sense(auto_sense, message);
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
  check_preset_export(synthetic);
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
  if (!bytes.empty()) {
    const auto target = static_cast<octa::InitializationTarget>(bytes.front());
    require(octa::encode_initialization(target).has_value() == (bytes.front() <= 4));
  }
  device_status(bytes);
  pcm_status(bytes);
  input_monitor(bytes);
  frames(bytes);
  structured(bytes);
  typed_controls(bytes);
  meter_packets(bytes);
  auto_sense_controls(bytes);
  preset_files(bytes);
  preset_imports(bytes);
  return 0;
}
