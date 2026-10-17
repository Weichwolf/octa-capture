// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Bridge.hpp"

#include "octa/DspControl.hpp"
#include "octa/Preamp.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>
#include <system_error>

namespace octa {
namespace {

using Values = std::span<const std::uint32_t>;

bool separator(char value) {
  return value == ' ' || value == '\t' || value == '\r';
}

std::optional<std::size_t> tokenize(std::string_view line, std::span<std::string_view> tokens) {
  std::size_t count = 0;
  while (!line.empty()) {
    const auto *const first = std::ranges::find_if_not(line, separator);
    line.remove_prefix(static_cast<std::size_t>(first - line.begin()));
    if (line.empty()) { break; }
    const auto *const last = std::ranges::find_if(line, separator);
    const auto size = static_cast<std::size_t>(last - line.begin());
    if (count == tokens.size()) { return std::nullopt; }
    tokens[count] = line.substr(0, size);
    ++count;
    line.remove_prefix(size);
  }
  return count;
}

std::optional<std::uint32_t> number(std::string_view text) {
  if (text.empty() ||
      !std::ranges::all_of(text, [](char value) { return value >= '0' && value <= '9'; })) {
    return std::nullopt;
  }
  std::uint32_t value = 0;
  const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
  if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) { return std::nullopt; }
  return value;
}

bool bytes(Values values) {
  constexpr std::uint32_t maximum_byte = 255;
  return std::ranges::all_of(values, [](auto value) { return value <= maximum_byte; });
}

std::optional<DspChange> parse_dsp(std::string_view operation, Values args) {
  if ((operation == "mixer" || operation == "master") && args.size() == 5 && bytes(args.first(4))) {
    const auto source = static_cast<MixerSource>(args[0]);
    const auto bus = static_cast<std::uint8_t>(args[1]);
    const auto channel = static_cast<std::uint8_t>(args[2]);
    if (operation == "mixer") {
      return MixerChange{.source = source,
                         .bus = bus,
                         .channel = channel,
                         .parameter = static_cast<MixerParameter>(args[3]),
                         .value = args[4]};
    }
    return MasterChange{.source = source,
                        .bus = bus,
                        .channel = channel,
                        .parameter = static_cast<MasterParameter>(args[3]),
                        .value = args[4]};
  }
  if (operation == "reverb" && args.size() == 3 && bytes(args)) {
    return ReverbChange{.parameter = static_cast<ReverbParameter>(args[0]),
                        .bank = static_cast<std::uint8_t>(args[1]),
                        .value = static_cast<std::uint8_t>(args[2])};
  }
  if (operation == "route" && args.size() == 2 && bytes(args)) {
    return RouteChange{.pair = static_cast<std::uint8_t>(args[0]),
                       .selection = static_cast<std::uint8_t>(args[1])};
  }
  return std::nullopt;
}

std::optional<BridgeCommand> command(std::string_view operation, Values args) {
  if (operation == "state" && args.empty()) { return StateQuery{}; }
  if (operation == "meters" && args.size() == 1 && args.front() <= 1) {
    return MeterSwitch{.enabled = args.front() != 0};
  }
  if (operation == "preamp" && args.size() == 3 && bytes(args)) {
    const PreampChange change = {.channel = static_cast<std::uint8_t>(args[0]),
                                 .parameter = static_cast<PreampParameter>(args[1]),
                                 .value = static_cast<std::uint8_t>(args[2])};
    if (encode_preamp_change(change)) { return change; }
    return std::nullopt;
  }
  const auto change = parse_dsp(operation, args);
  if (change && encode_dsp_change(*change)) { return *change; }
  return std::nullopt;
}

}

std::optional<BridgeRequest> parse_bridge_request(std::string_view line) {
  if (line.empty() || line.size() > bridge_line_limit) { return std::nullopt; }
  constexpr std::size_t maximum_tokens = 7;
  std::array<std::string_view, maximum_tokens> tokens{};
  const auto count = tokenize(line, tokens);
  if (!count || *count < 2) { return std::nullopt; }
  const auto serial = number(tokens[0]);
  if (!serial || *serial == 0) { return std::nullopt; }
  std::array<std::uint32_t, maximum_tokens - 2> values{};
  for (std::size_t index = 2; index < *count; ++index) {
    const auto value = number(tokens[index]);
    if (!value) { return std::nullopt; }
    values[index - 2] = *value;
  }
  const auto parsed = command(tokens[1], std::span(values).first(*count - 2));
  if (!parsed) { return std::nullopt; }
  return BridgeRequest{.serial = *serial, .command = *parsed};
}

}
