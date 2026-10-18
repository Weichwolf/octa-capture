// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Bridge.hpp"
#include "octa/DspControl.hpp"
#include "octa/InputMonitor.hpp"
#include "octa/PanelCommand.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <type_traits>
#include <variant>

namespace {

bool valid(const octa::BridgeCommand &command) {
  return std::visit(
      [](const auto &change) {
        using Change = std::remove_cvref_t<decltype(change)>;
        if constexpr (std::is_same_v<Change, octa::PreampChange>) {
          const auto encoded = octa::encode_preamp_change(change);
          return encoded && octa::decode(*encoded).has_value();
        } else if constexpr (std::is_same_v<Change, octa::DspChange>) {
          const auto encoded = octa::encode_dsp_change(change);
          return encoded && octa::decode(*encoded).has_value();
        } else if constexpr (std::is_same_v<Change, octa::PanelCommand>) {
          const auto encoded = octa::encode_panel_command(change);
          return encoded && octa::decode(*encoded).has_value();
        } else if constexpr (std::is_same_v<Change, octa::InputMonitorChange>) {
          const auto encoded = octa::encode_input_monitor(change);
          return encoded && octa::decode(*encoded).has_value() &&
                 (!change.channel || *change.channel < 10);
        } else {
          return true;
        }
      },
      command);
}

}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size);

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
  const std::string_view text(reinterpret_cast<const char *>(data), size);
  const auto request = octa::parse_bridge_request(text);
  if (request && (request->serial == 0 || !valid(request->command))) { __builtin_trap(); }
  return 0;
}
