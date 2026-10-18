// SPDX-License-Identifier: GPL-2.0-only
#include "octa/StateJson.hpp"

#include "octa/AutoSense.hpp"
#include "octa/Dsp.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <ios>
#include <ostream>

namespace {

template <typename Value, std::size_t size>
void print_numbers(std::ostream &out, const std::array<Value, size> &values) {
  out << '[';
  for (const auto &value : values) {
    if (&value != &values.front()) { out << ','; }
    out << static_cast<unsigned int>(value);
  }
  out << ']';
}

void print_group(std::ostream &out, const octa::MixerGroup &group) {
  out << R"({"channels":[)";
  for (const auto &channel : group.channels) {
    if (&channel != &group.channels.front()) { out << ','; }
    out << std::boolalpha << R"({"mute":)" << channel.mute << R"(,"solo":)" << channel.solo
        << R"(,"pan":)" << channel.pan << R"(,"volume":)" << channel.volume << '}';
  }
  out << R"(],"pairs":[)";
  for (const auto &pair : group.pairs) {
    if (&pair != &group.pairs.front()) { out << ','; }
    out << R"({"linked":)" << pair.linked << R"(,"mono":)" << pair.mono << '}';
  }
  out << "]}";
}

void print_master(std::ostream &out, const octa::MixerMaster &master) {
  out << R"({"linked":)" << master.linked << R"(,"volume":)";
  print_numbers(out, master.volume);
  out << '}';
}

void print_dsp(std::ostream &out, const octa::DspState &state) {
  out << R"(,"mixers":[)";
  for (const auto &mixer : state.mixers) {
    if (&mixer != &state.mixers.front()) { out << ','; }
    out << R"({"input":)";
    print_group(out, mixer.input);
    out << R"(,"playback":)";
    print_group(out, mixer.playback);
    out << R"(,"input_master":)";
    print_master(out, mixer.input_master);
    out << R"(,"playback_master":)";
    print_master(out, mixer.playback_master);
    out << R"(,"input_output_link":)" << mixer.input_output_link << '}';
  }
  const auto &reverb = state.reverb;
  out << R"(],"reverb":{"type":)" << static_cast<unsigned int>(reverb.type)
      << R"(,"pre_delay_indices":)";
  print_numbers(out, reverb.pre_delay_indices);
  out << R"(,"time_indices":)";
  print_numbers(out, reverb.time_indices);
  out << R"(,"input_send":)";
  print_numbers(out, reverb.input_send);
  out << R"(,"master_return":)" << reverb.master_return << R"(},"output_routes":)";
  print_numbers(out, state.output_routes);
}

void print_preamp(std::ostream &out, const octa::PreampState &preamp) {
  const auto &comp = preamp.compressor;
  out << std::boolalpha << R"({"phantom":)" << preamp.phantom << R"(,"phase_inverted":)"
      << preamp.phase_inverted << R"(,"hi_z":)" << preamp.hi_z << R"(,"low_cut":)" << preamp.low_cut
      << R"(,"sensitivity_index":)" << static_cast<unsigned int>(preamp.sensitivity_index)
      << R"(,"compressor":{"enabled":)" << comp.enabled << R"(,"gate_index":)"
      << static_cast<unsigned int>(comp.gate_index) << R"(,"attack_index":)"
      << static_cast<unsigned int>(comp.attack_index) << R"(,"release_index":)"
      << static_cast<unsigned int>(comp.release_index) << R"(,"threshold_index":)"
      << static_cast<unsigned int>(comp.threshold_index) << R"(,"ratio_index":)"
      << static_cast<unsigned int>(comp.ratio_index) << R"(,"gain_index":)"
      << static_cast<unsigned int>(comp.gain_index) << "}}";
}

}

namespace octa {

bool write_auto_sense_activity_json(std::ostream &out, const Message &message) {
  AutoSenseChannels channels{};
  if (!apply_auto_sense_update(channels, message)) { return false; }
  std::uint32_t mask = 0;
  for (std::size_t channel = 0; channel < channels.size(); ++channel) {
    if (channels[channel]) { mask |= 1U << channel; }
  }
  out << std::dec << R"({"event":"auto-sense","channels":)" << mask << R"(,"active":)"
      << std::boolalpha << (mask != 0) << '}';
  return out.good();
}

bool write_state_json(std::ostream &out, const Message &message) {
  const auto bank = decode_preamps(message);
  const auto dsp = decode_dsp(message);
  if (!bank || !dsp) { return false; }
  out << R"({"payload_hex":")" << std::hex << std::setfill('0');
  for (const auto byte : message.payload) {
    out << std::setw(2) << static_cast<unsigned int>(byte);
  }
  out << std::dec << R"(","preamps":[)";
  for (const auto &preamp : bank->channels) {
    if (&preamp != &bank->channels.front()) { out << ','; }
    print_preamp(out, preamp);
  }
  out << R"(],"stereo_links":[)";
  bool first = true;
  for (const auto linked : bank->stereo_links) {
    if (!first) { out << ','; }
    out << std::boolalpha << linked;
    first = false;
  }
  out << ']';
  print_dsp(out, *dsp);
  out << '}';
  return out.good();
}

}
