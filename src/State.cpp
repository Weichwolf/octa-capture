// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Dsp.hpp"
#include "octa/Midi.hpp"
#include "octa/Preamp.hpp"
#include "octa/Protocol.hpp"

#include <array>
#include <cstddef>
#include <iomanip>
#include <iostream>
#include <string_view>

namespace {

template <typename Value, std::size_t size>
void print_numbers(const std::array<Value, size> &values) {
  std::cout << '[';
  for (const auto &value : values) {
    if (&value != &values.front()) { std::cout << ','; }
    std::cout << static_cast<unsigned int>(value);
  }
  std::cout << ']';
}

void print_group(const octa::MixerGroup &group) {
  std::cout << R"({"channels":[)";
  for (const auto &channel : group.channels) {
    if (&channel != &group.channels.front()) { std::cout << ','; }
    std::cout << std::boolalpha << R"({"mute":)" << channel.mute << R"(,"solo":)" << channel.solo
              << R"(,"pan":)" << channel.pan << R"(,"volume":)" << channel.volume << '}';
  }
  std::cout << R"(],"pairs":[)";
  for (const auto &pair : group.pairs) {
    if (&pair != &group.pairs.front()) { std::cout << ','; }
    std::cout << R"({"linked":)" << pair.linked << R"(,"mono":)" << pair.mono << '}';
  }
  std::cout << "]}";
}

void print_master(const octa::MixerMaster &master) {
  std::cout << R"({"linked":)" << master.linked << R"(,"volume":)";
  print_numbers(master.volume);
  std::cout << '}';
}

void print_dsp(const octa::DspState &state) {
  std::cout << R"(,"mixers":[)";
  for (const auto &mixer : state.mixers) {
    if (&mixer != &state.mixers.front()) { std::cout << ','; }
    std::cout << R"({"input":)";
    print_group(mixer.input);
    std::cout << R"(,"playback":)";
    print_group(mixer.playback);
    std::cout << R"(,"input_master":)";
    print_master(mixer.input_master);
    std::cout << R"(,"playback_master":)";
    print_master(mixer.playback_master);
    std::cout << R"(,"input_output_link":)" << mixer.input_output_link << '}';
  }
  const auto &reverb = state.reverb;
  std::cout << R"(],"reverb":{"type":)" << static_cast<unsigned int>(reverb.type)
            << R"(,"pre_delay_indices":)";
  print_numbers(reverb.pre_delay_indices);
  std::cout << R"(,"time_indices":)";
  print_numbers(reverb.time_indices);
  std::cout << R"(,"input_send":)";
  print_numbers(reverb.input_send);
  std::cout << R"(,"master_return":)" << reverb.master_return << R"(},"output_routes":)";
  print_numbers(state.output_routes);
}

void print_preamp(const octa::PreampState &preamp) {
  const auto &comp = preamp.compressor;
  std::cout << std::boolalpha << R"({"phantom":)" << preamp.phantom << R"(,"phase_inverted":)"
            << preamp.phase_inverted << R"(,"hi_z":)" << preamp.hi_z << R"(,"low_cut":)"
            << preamp.low_cut << R"(,"sensitivity_index":)"
            << static_cast<unsigned int>(preamp.sensitivity_index) << R"(,"compressor":{"enabled":)"
            << comp.enabled << R"(,"gate_index":)" << static_cast<unsigned int>(comp.gate_index)
            << R"(,"attack_index":)" << static_cast<unsigned int>(comp.attack_index)
            << R"(,"release_index":)" << static_cast<unsigned int>(comp.release_index)
            << R"(,"threshold_index":)" << static_cast<unsigned int>(comp.threshold_index)
            << R"(,"ratio_index":)" << static_cast<unsigned int>(comp.ratio_index)
            << R"(,"gain_index":)" << static_cast<unsigned int>(comp.gain_index) << "}}";
}

}

int main(int argc, char **argv) {
  if (argc > 2) {
    std::cerr << "Usage: octa-state [hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1]\n";
    return 2;
  }
  const std::string_view port = argc == 2 ? argv[1] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  const auto state = octa::read_bulk_state(port);
  if (!state) {
    std::cerr << state.error().operation << ": ALSA error " << state.error().code << '\n';
    return 1;
  }
  const auto bank = octa::decode_preamps(*state);
  const auto dsp = octa::decode_dsp(*state);
  if (!bank || !dsp) {
    std::cerr << "Invalid preamp/DSP state in bulk reply.\n";
    return 1;
  }
  std::cout << R"({"payload_hex":")" << std::hex << std::setfill('0');
  for (const auto byte : state->payload) {
    std::cout << std::setw(2) << static_cast<unsigned int>(byte);
  }
  std::cout << std::dec << R"(","preamps":[)";
  for (const auto &preamp : bank->channels) {
    if (&preamp != &bank->channels.front()) { std::cout << ','; }
    print_preamp(preamp);
  }
  std::cout << R"(],"stereo_links":[)";
  bool first = true;
  for (const auto linked : bank->stereo_links) {
    if (!first) { std::cout << ','; }
    std::cout << std::boolalpha << linked;
    first = false;
  }
  std::cout << ']';
  print_dsp(*dsp);
  std::cout << "}\n";
  return 0;
}
