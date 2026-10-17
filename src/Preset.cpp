// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Preset.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <span>
#include <utility>
#include <vector>

namespace octa {
namespace {

constexpr auto magic = std::to_array("OCTA-CAPTURE Control Panel Data");
constexpr auto version = std::to_array("Ver100");
constexpr std::size_t header_size = magic.size() + version.size();
constexpr std::size_t payload_size = 1600;
static_assert(preset_size == header_size + payload_size);
constexpr unsigned int byte_bits = 8;
constexpr unsigned int word_bits = 32;

class Reader {
public:
  explicit Reader(std::span<const std::uint8_t> bytes) : bytes_(bytes) {}

  void flag(bool &value) { value = byte() != 0; }

  void word(std::uint32_t &value) {
    value = 0;
    for (unsigned int shift = 0; shift < word_bits; shift += byte_bits) {
      value |= static_cast<std::uint32_t>(byte()) << shift;
    }
  }

  [[nodiscard]] bool finished() const { return !failed_ && bytes_.empty(); }

private:
  std::uint8_t byte() {
    if (bytes_.empty()) {
      failed_ = true;
      return 0;
    }
    const auto value = bytes_.front();
    bytes_ = bytes_.subspan(1);
    return value;
  }

  std::span<const std::uint8_t> bytes_;
  bool failed_ = false;
};

class Writer {
public:
  Writer() {
    bytes_.reserve(preset_size);
    for (const auto byte : magic) { bytes_.push_back(static_cast<std::uint8_t>(byte)); }
    for (const auto byte : version) { bytes_.push_back(static_cast<std::uint8_t>(byte)); }
  }

  void flag(bool value) { bytes_.push_back(static_cast<std::uint8_t>(value)); }

  void word(std::uint32_t value) {
    constexpr std::uint32_t byte_mask = 255;
    for (unsigned int shift = 0; shift < word_bits; shift += byte_bits) {
      bytes_.push_back(static_cast<std::uint8_t>((value >> shift) & byte_mask));
    }
  }

  [[nodiscard]] std::vector<std::uint8_t> take() { return std::move(bytes_); }

private:
  std::vector<std::uint8_t> bytes_;
};

template <typename Archive, typename Channel>
void transfer_preamp(Archive &archive, Channel &channel) {
  archive.flag(channel.compressor.enabled);
  for (auto &index : channel.compressor.indices) { archive.word(index); }
  archive.flag(channel.phantom);
  archive.flag(channel.phase_inverted);
  archive.flag(channel.hi_z);
  archive.flag(channel.low_cut);
  archive.word(channel.sensitivity_index);
}

template <typename Archive, typename Channel>
void transfer_channel(Archive &archive, Channel &channel) {
  archive.flag(channel.mute);
  archive.flag(channel.solo);
  archive.word(channel.reverb_send);
  archive.word(channel.pan);
  archive.word(channel.volume);
}

template <typename Archive, typename Group> void transfer_group(Archive &archive, Group &group) {
  for (auto &pair : group) {
    archive.flag(pair.linked);
    for (auto &channel : pair.channels) { transfer_channel(archive, channel); }
  }
}

template <typename Archive, typename Master>
void transfer_master(Archive &archive, Master &master) {
  archive.flag(master.linked);
  archive.word(master.reverb_return);
  for (auto &volume : master.volume) { archive.word(volume); }
}

template <typename Archive, typename State> void transfer(Archive &archive, State &state) {
  for (auto &route : state.output_routes) { archive.word(route); }
  archive.word(state.reverb.type);
  for (std::size_t bank = 0; bank < state.reverb.pre_delay_indices.size(); ++bank) {
    archive.word(state.reverb.pre_delay_indices[bank]);
    archive.word(state.reverb.time_indices[bank]);
  }
  for (auto &pair : state.preamps) {
    archive.flag(pair.linked);
    for (auto &channel : pair.channels) { transfer_preamp(archive, channel); }
  }
  for (auto &mixer : state.mixers) {
    archive.flag(mixer.input_output_link);
    transfer_group(archive, mixer.input);
    transfer_group(archive, mixer.playback);
    transfer_master(archive, mixer.input_master);
    transfer_master(archive, mixer.playback_master);
  }
}

}

std::expected<Preset, PresetError> decode_preset(std::span<const std::uint8_t> bytes) {
  if (bytes.size() != preset_size) { return std::unexpected(PresetError::size); }
  if (!std::ranges::equal(bytes.first(magic.size()), magic)) {
    return std::unexpected(PresetError::header);
  }
  if (!std::ranges::equal(bytes.subspan(magic.size(), version.size()), version)) {
    return std::unexpected(PresetError::version);
  }
  Reader reader(bytes.subspan(header_size));
  Preset state{};
  transfer(reader, state);
  if (!reader.finished()) { return std::unexpected(PresetError::size); }
  return state;
}

std::vector<std::uint8_t> encode_preset(const Preset &preset) {
  Writer writer;
  transfer(writer, preset);
  return writer.take();
}

}
