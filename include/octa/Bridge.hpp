// SPDX-License-Identifier: GPL-2.0-only
#pragma once

#include "octa/DspControl.hpp"
#include "octa/InputMonitor.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/Preamp.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string_view>
#include <variant>

namespace octa {

struct StateQuery {};

struct MeterSwitch {
  bool enabled;
};

using BridgeCommand = std::
    variant<StateQuery, MeterSwitch, PreampChange, DspChange, PanelCommand, InputMonitorChange>;

struct BridgeRequest {
  std::uint32_t serial;
  BridgeCommand command;
};

inline constexpr std::size_t bridge_line_limit = 512;

// Private line protocol for the local web process: serial, operation and decimal
// wire selectors. No paths, shell commands or manufacturer packets are accepted.
// Parse and enforce existing typed write bounds before device access.
[[nodiscard]] std::optional<BridgeRequest> parse_bridge_request(std::string_view line);

}
