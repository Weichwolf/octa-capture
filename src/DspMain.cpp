// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DspControl.hpp"
#include "octa/Midi.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <span>
#include <string_view>
#include <system_error>
#include <utility>

namespace {

using Arguments = std::span<char *const>;

std::optional<std::uint32_t> number(std::string_view text) {
  std::uint32_t value = 0;
  const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
  if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) { return std::nullopt; }
  return value;
}

std::optional<std::uint8_t> byte_number(std::string_view text) {
  const auto value = number(text);
  if (!value || *value > std::numeric_limits<std::uint8_t>::max()) { return std::nullopt; }
  return static_cast<std::uint8_t>(*value);
}

template <typename Value, std::size_t size>
std::optional<Value> lookup(std::string_view text,
                            const std::array<std::pair<std::string_view, Value>, size> &table) {
  const auto *const found =
      std::ranges::find(table, text, &std::pair<std::string_view, Value>::first);
  if (found == table.end()) { return std::nullopt; }
  return found->second;
}

std::optional<octa::MixerSource> source(std::string_view text) {
  constexpr std::array<std::pair<std::string_view, octa::MixerSource>, 2> table = {
      {{"input", octa::MixerSource::input}, {"playback", octa::MixerSource::playback}}};
  return lookup(text, table);
}

std::optional<std::uint8_t> bus(std::string_view text) {
  if (text.size() != 1 || text.front() < 'A' || text.front() > 'D') { return std::nullopt; }
  return static_cast<std::uint8_t>(text.front() - 'A');
}

std::optional<octa::DspChange> mixer(Arguments args) {
  constexpr std::array<std::pair<std::string_view, octa::MixerParameter>, 7> parameters = {
      {{"link", octa::MixerParameter::stereo_link},
       {"mono", octa::MixerParameter::mono},
       {"solo", octa::MixerParameter::solo},
       {"mute", octa::MixerParameter::mute},
       {"pan", octa::MixerParameter::pan},
       {"volume", octa::MixerParameter::volume},
       {"send", octa::MixerParameter::reverb_send}}};
  const auto group = source(args[1]);
  const auto mix_bus = bus(args[2]);
  const auto channel = byte_number(args[3]);
  const auto parameter = lookup(args[4], parameters);
  const auto value = number(args[5]);
  if (!group || !mix_bus || !channel || *channel == 0 || !parameter || !value) {
    return std::nullopt;
  }
  return octa::MixerChange{.source = *group,
                           .bus = *mix_bus,
                           .channel = static_cast<std::uint8_t>(*channel - 1),
                           .parameter = *parameter,
                           .value = *value};
}

std::optional<octa::DspChange> master(Arguments args) {
  constexpr std::array<std::pair<std::string_view, octa::MasterParameter>, 4> parameters = {
      {{"link", octa::MasterParameter::stereo_link},
       {"volume", octa::MasterParameter::volume},
       {"return", octa::MasterParameter::reverb_return},
       {"io-link", octa::MasterParameter::input_output_link}}};
  const auto group = source(args[1]);
  const auto mix_bus = bus(args[2]);
  const auto channel = byte_number(args[3]);
  const auto parameter = lookup(args[4], parameters);
  const auto value = number(args[5]);
  if (!group || !mix_bus || !channel || *channel == 0 || !parameter || !value) {
    return std::nullopt;
  }
  return octa::MasterChange{.source = *group,
                            .bus = *mix_bus,
                            .channel = static_cast<std::uint8_t>(*channel - 1),
                            .parameter = *parameter,
                            .value = *value};
}

std::optional<octa::DspChange> reverb(Arguments args) {
  constexpr std::array<std::pair<std::string_view, octa::ReverbParameter>, 3> parameters = {
      {{"type", octa::ReverbParameter::type},
       {"pre-delay", octa::ReverbParameter::pre_delay},
       {"time", octa::ReverbParameter::time}}};
  const auto parameter = lookup(args[1], parameters);
  const auto bank = byte_number(args[2]);
  const auto value = byte_number(args[3]);
  if (!parameter || !bank || !value) { return std::nullopt; }
  return octa::ReverbChange{.parameter = *parameter, .bank = *bank, .value = *value};
}

std::optional<octa::DspChange> route(Arguments args) {
  const auto pair = byte_number(args[1]);
  const auto selection = byte_number(args[2]);
  if (!pair || *pair == 0 || !selection) { return std::nullopt; }
  return octa::RouteChange{.pair = static_cast<std::uint8_t>(*pair - 1), .selection = *selection};
}

void usage() {
  std::cerr << "Usage: octa-dsp [--dry-run] COMMAND ... [hw:CTRL-port]\n"
               "  mixer input|playback A|B|C|D CHANNEL link|mono|solo|mute|pan|volume|send VALUE\n"
               "  master input|playback A|B|C|D CHANNEL link|volume|return|io-link VALUE\n"
               "  reverb type|pre-delay|time BANK VALUE\n"
               "  route PAIR SELECTION\n"
               "Channels/pairs start at 1; master channels are 1/2 (L/R).\n"
               "Link/mono use odd channel numbers; master switches use channel 1.\n"
               "Reverb type uses bank 0; pre-delay/time use type banks 1..4.\n"
               "Values are decimal wire indices/coefficients. Dry run opens no device.\n";
}

struct Command {
  std::string_view name;
  std::size_t arguments;
  std::optional<octa::DspChange> (*parse)(Arguments);
};

}

int main(int argc, char **argv) {
  auto args = Arguments(argv, static_cast<std::size_t>(argc)).subspan(1);
  const bool dry_run = !args.empty() && std::string_view(args.front()) == "--dry-run";
  if (dry_run) { args = args.subspan(1); }
  if (args.empty()) {
    usage();
    return 2;
  }
  constexpr std::array<Command, 4> commands = {
      {{.name = "mixer", .arguments = 6, .parse = &mixer},
       {.name = "master", .arguments = 6, .parse = &master},
       {.name = "reverb", .arguments = 4, .parse = &reverb},
       {.name = "route", .arguments = 3, .parse = &route}}};
  const auto *const command =
      std::ranges::find(commands, std::string_view(args.front()), &Command::name);
  if (command == commands.end() || args.size() < command->arguments ||
      args.size() > command->arguments + 1) {
    usage();
    return 2;
  }
  const auto change = command->parse(args.first(command->arguments));
  if (!change) {
    usage();
    return 2;
  }
  const auto frame = octa::encode_dsp_change(*change);
  if (!frame) {
    std::cerr << "Invalid DSP selector or value.\n";
    return 2;
  }
  if (dry_run) {
    std::cout << R"({"frame_hex":")" << std::hex << std::setfill('0');
    for (const auto byte : *frame) { std::cout << std::setw(2) << static_cast<unsigned int>(byte); }
    std::cout << "\"}\n";
    return 0;
  }
  const std::string_view port =
      args.size() > command->arguments ? args.back() : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  const auto result = octa::write_dsp(port, *change);
  if (!result) {
    std::cerr << result.error().operation << ": ALSA error " << result.error().code << '\n';
    return 1;
  }
  std::cout << "{\"verified\":true}\n";
  return 0;
}
