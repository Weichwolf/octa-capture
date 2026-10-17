// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/PcmMeters.hpp"
#include "octa/StatusControl.hpp"

#include <expected>
#include <memory>
#include <string_view>

namespace octa {

// Thread-confined, validated CARD controls only; never opens a PCM file. Reads
// renew a two-second kernel lease. Destruction disables our meter subscription.
class PcmMeterControl {
public:
  [[nodiscard]] static std::expected<PcmMeterControl, StatusReadError> open(std::string_view port);
  ~PcmMeterControl();
  PcmMeterControl(PcmMeterControl &&) noexcept;
  PcmMeterControl &operator=(PcmMeterControl &&) noexcept;
  PcmMeterControl(const PcmMeterControl &) = delete;
  PcmMeterControl &operator=(const PcmMeterControl &) = delete;
  [[nodiscard]] std::expected<void, StatusReadError> enable(bool enabled);
  [[nodiscard]] std::expected<PcmMeters, StatusReadError> read();

private:
  struct Impl;
  explicit PcmMeterControl(std::unique_ptr<Impl> impl);
  std::unique_ptr<Impl> impl_;
};

}
