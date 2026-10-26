// SPDX-License-Identifier: GPL-2.0-only
#include "octa/PairPcm.hpp"

#include <algorithm>
#include <alsa/asoundlib.h>
#include <array>
#include <cerrno>
#include <cstddef>
#include <expected>
#include <memory>
#include <string>
#include <string_view>

namespace octa {
namespace {
constexpr int maximum_card_index = 255;

bool valid_card(std::string_view card) {
  return !card.empty() && card.size() <= 15 && std::ranges::all_of(card, [](char value) {
    return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z') ||
           (value >= '0' && value <= '9') || value == '_';
  });
}

std::expected<int, PairPcmError> card_index(snd_pcm_t *pcm) {
  snd_pcm_info_t *raw = nullptr;
  const int allocated = snd_pcm_info_malloc(&raw);
  const std::unique_ptr<snd_pcm_info_t, decltype(&snd_pcm_info_free)> info(raw, &snd_pcm_info_free);
  if (allocated < 0 || !info) {
    return std::unexpected(PairPcmError{.code = allocated < 0 ? allocated : -EIO,
                                        .operation = "allocate paired PCM metadata"});
  }
  const int queried = snd_pcm_info(pcm, info.get());
  if (queried < 0) {
    return std::unexpected(PairPcmError{.code = queried, .operation = "query paired PCM metadata"});
  }
  const int card = snd_pcm_info_get_card(info.get());
  if (card < 0 || card > maximum_card_index || snd_pcm_info_get_device(info.get()) != 0 ||
      snd_pcm_info_get_subdevice(info.get()) != 0) {
    return std::unexpected(
        PairPcmError{.code = -EPROTO, .operation = "paired hardware PCM metadata"});
  }
  return card;
}

}

void PairPcmReservation::ClosePcm::operator()(snd_pcm_t *pcm) const noexcept {
  snd_pcm_close(pcm);
}

std::expected<PairPcmReservation, PairPcmError>
PairPcmReservation::open(const std::array<std::string_view, 2> &card_ids) {
  if (!valid_card(card_ids[0]) || !valid_card(card_ids[1]) || card_ids[0] == card_ids[1]) {
    return std::unexpected(
        PairPcmError{.code = -EINVAL, .operation = "distinct paired ALSA cards"});
  }
  PairPcmReservation result;
  constexpr std::array streams = {SND_PCM_STREAM_PLAYBACK, SND_PCM_STREAM_CAPTURE};
  for (std::size_t unit = 0; unit < card_ids.size(); ++unit) {
    const auto name = "hw:CARD=" + std::string(card_ids[unit]) + ",DEV=0,SUBDEV=0";
    for (std::size_t direction = 0; direction < streams.size(); ++direction) {
      snd_pcm_t *raw = nullptr;
      const int opened = snd_pcm_open(&raw, name.c_str(), streams[direction], SND_PCM_NONBLOCK);
      auto &pcm = result.handles_[(unit * 2) + direction];
      pcm.reset(raw);
      if (opened < 0 || !pcm) {
        return std::unexpected(PairPcmError{.code = opened < 0 ? opened : -EIO,
                                            .operation = "reserve paired hardware PCM"});
      }
      const auto card = card_index(pcm.get());
      if (!card) { return std::unexpected(card.error()); }
      if (direction == 0) { result.cards_[unit] = *card; }
      if (*card != result.cards_[unit] || (unit == 1 && *card == result.cards_[0])) {
        return std::unexpected(
            PairPcmError{.code = -EXDEV, .operation = "distinct consistent paired PCM identities"});
      }
    }
  }
  return result;
}

snd_pcm_t *PairPcmReservation::capture(PairUnit unit) const noexcept {
  switch (unit) {
    case PairUnit::base: return handles_[1].get();
    case PairUnit::expansion: return handles_[3].get();
    default: return nullptr;
  }
}

const std::array<int, 2> &PairPcmReservation::card_indices() const noexcept {
  return cards_;
}

}
