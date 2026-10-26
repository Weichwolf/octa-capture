// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include <alsa/asoundlib.h>
#include <array>
#include <cstdint>
#include <expected>
#include <memory>
#include <string_view>

namespace octa {

enum class PairUnit : std::uint8_t { base, expansion };

struct PairPcmError {
  int code;
  std::string_view operation;
};

// Pins both native capture/playback PCMs before a paired clock operation.
// Does not configure, prepare, start, read or write either stream. The caller
// must separately verify USB identities, device roles and coherent clocks.
class PairPcmReservation {
  struct ClosePcm {
    void operator()(snd_pcm_t *pcm) const noexcept;
  };

  using Pcm = std::unique_ptr<snd_pcm_t, ClosePcm>;
  std::array<Pcm, 4> handles_;
  std::array<int, 2> cards_ = {-1, -1};
  PairPcmReservation() = default;

public:
  PairPcmReservation(const PairPcmReservation &) = delete;
  PairPcmReservation &operator=(const PairPcmReservation &) = delete;
  PairPcmReservation(PairPcmReservation &&) noexcept = default;
  PairPcmReservation &operator=(PairPcmReservation &&) noexcept = default;
  ~PairPcmReservation() = default;

  [[nodiscard]] static std::expected<PairPcmReservation, PairPcmError>
  open(const std::array<std::string_view, 2> &card_ids);
  [[nodiscard]] snd_pcm_t *capture(PairUnit unit) const noexcept;
  // ALSA can reuse a card number after disconnect while old handles remain
  // open. Check both held directions instead of assuming a number stays pinned.
  [[nodiscard]] std::expected<void, PairPcmError> check(PairUnit unit) const;
  [[nodiscard]] const std::array<int, 2> &card_indices() const noexcept;
};

}
