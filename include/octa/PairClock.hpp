// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/PairPcm.hpp"
#include "octa/StatusControl.hpp"

#include <chrono>
#include <cstdint>
#include <expected>
#include <optional>

namespace octa {

// The transport owns reservations for both PCM directions of both verified
// USB identities throughout the operation, including recovery.
class PairClockIo {
public:
  virtual ~PairClockIo() = default;
  [[nodiscard]] virtual std::expected<DeviceStatus, StatusReadError> read(PairUnit unit) = 0;
  [[nodiscard]] virtual std::expected<void, StatusReadError>
  prepare_expansion(std::uint32_t rate) = 0;
  [[nodiscard]] virtual std::chrono::steady_clock::time_point now() const noexcept = 0;
  virtual void pause(std::chrono::milliseconds delay) = 0;
  [[nodiscard]] virtual bool cancelled() const noexcept = 0;
};

struct PairClockResult {
  std::uint32_t previous_rate;
  std::uint32_t rate;
  bool changed;
};

struct PairClockFailure {
  StatusReadError cause;
  // Empty after a write attempt means recovery was verified. Before any write
  // attempt, no recovery is needed. A populated error means recovery failed.
  std::optional<StatusReadError> recovery;
  bool change_attempted = false;
};

[[nodiscard]] std::expected<PairClockResult, PairClockFailure>
change_pair_clock(PairClockIo &io, std::uint32_t rate);

}
