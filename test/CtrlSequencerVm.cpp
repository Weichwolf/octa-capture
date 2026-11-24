// SPDX-License-Identifier: GPL-2.0-only
// Real ALSA sequencer regression checks, restricted to the isolated dummy USB VM.
#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <sound/asequencer.h>
#include <sound/asound.h>
#include <string_view>
#include <sys/ioctl.h>
#include <sys/utsname.h>
#include <unistd.h>

namespace {
constexpr unsigned int midi_io = SNDRV_SEQ_PORT_CAP_READ | SNDRV_SEQ_PORT_CAP_WRITE |
                                 SNDRV_SEQ_PORT_CAP_SUBS_READ | SNDRV_SEQ_PORT_CAP_SUBS_WRITE;
enum class Direction : std::uint8_t { from_device, to_device };

void require(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAIL " << message << " (errno=" << errno << ")\n";
    std::_Exit(1);
  }
}

int checked(int result, const char *message) {
  require(result >= 0, message);
  return result;
}

void guard() {
  utsname system{};
  checked(uname(&system), "Read kernel identity");
  require(std::string_view(system.release).ends_with("-ctrl-seq-octa-kasan-vm"),
          "Private sequencer VM required");
  snd_ctl_card_info card{};
  const int control = checked(open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC), "Open modeled card");
  checked(ioctl(control, SNDRV_CTL_IOCTL_CARD_INFO, &card), "Read card identity");
  require(std::string_view(reinterpret_cast<const char *>(card.longname)).contains("dummy_hcd"),
          "Dummy USB card required");
  checked(close(control), "Close card identity handle");
}

struct Sequencer {
  int fd;
  std::uint8_t client;
  std::uint8_t device;
};

Sequencer open_sequencer() {
  const int fd = checked(open("/dev/snd/seq", O_RDWR | O_NONBLOCK | O_CLOEXEC), "Open sequencer");
  int own = 0;
  checked(ioctl(fd, SNDRV_SEQ_IOCTL_CLIENT_ID, &own), "Get own sequencer client");
  require(own > 0 && own <= UINT8_MAX, "Bounded sequencer identity");
  snd_seq_client_info client{};
  client.client = -1;
  int device = -1;
  while (ioctl(fd, SNDRV_SEQ_IOCTL_QUERY_NEXT_CLIENT, &client) == 0) {
    if (client.type == KERNEL_CLIENT && client.card == 0) {
      device = client.client;
      break;
    }
  }
  require(device > 0 && device <= UINT8_MAX, "Modeled hardware sequencer client");
  snd_seq_port_info port{};
  port.addr.client = static_cast<std::uint8_t>(own);
  constexpr std::string_view name = "OCTA sequencer regression";
  std::ranges::copy(name, port.name);
  // Same private bridge capabilities used by JACK's ALSA sequencer driver.
  port.capability =
      SNDRV_SEQ_PORT_CAP_READ | SNDRV_SEQ_PORT_CAP_WRITE | SNDRV_SEQ_PORT_CAP_NO_EXPORT;
  port.type = SNDRV_SEQ_PORT_TYPE_APPLICATION;
  checked(ioctl(fd, SNDRV_SEQ_IOCTL_CREATE_PORT, &port), "Create private bridge port");
  require(port.addr.port == 0, "First bridge port is zero");
  return {.fd = fd,
          .client = static_cast<std::uint8_t>(own),
          .device = static_cast<std::uint8_t>(device)};
}

snd_seq_port_subscribe subscription(const Sequencer &seq, std::uint8_t port, Direction direction) {
  snd_seq_port_subscribe result{};
  const snd_seq_addr device{.client = seq.device, .port = port};
  const snd_seq_addr bridge{.client = seq.client, .port = 0};
  result.sender = direction == Direction::from_device ? device : bridge;
  result.dest = direction == Direction::from_device ? bridge : device;
  return result;
}

int open_raw(int subdevice) {
  const int control = checked(open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC), "Select raw MIDI");
  checked(ioctl(control, SNDRV_CTL_IOCTL_RAWMIDI_PREFER_SUBDEVICE, &subdevice), "Prefer raw port");
  const int fd = open("/dev/snd/midiC0D0", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
  const int error = errno;
  checked(close(control), "Close MIDI selector");
  if (fd >= 0) {
    snd_rawmidi_params params{};
    params.stream = SNDRV_RAWMIDI_STREAM_OUTPUT;
    params.buffer_size = 256;
    params.avail_min = 1;
    params.no_active_sensing = 1;
    checked(ioctl(fd, SNDRV_RAWMIDI_IOCTL_PARAMS, &params), "Configure raw MIDI close");
  }
  errno = error;
  return fd;
}

void check_raw_busy(int port) {
  const int fd = open_raw(port);
  require(fd == -1 && errno == EBUSY, "Bridge holds the requested raw MIDI port");
}

void check_raw_control_available() {
  const int fd = checked(open_raw(1), "Private raw CTRL remains available");
  checked(close(fd), "Close raw CTRL");
}

void private_policy(const Sequencer &seq) {
  snd_seq_port_info port{};
  port.addr = {.client = seq.device, .port = 0};
  checked(ioctl(seq.fd, SNDRV_SEQ_IOCTL_GET_PORT_INFO, &port), "Query DIN port policy");
  require((port.capability & midi_io) == midi_io &&
              (port.capability & SNDRV_SEQ_PORT_CAP_NO_EXPORT) == 0,
          "DIN MIDI retains public duplex and subscription capabilities");
  port.addr.port = 1;
  checked(ioctl(seq.fd, SNDRV_SEQ_IOCTL_GET_PORT_INFO, &port), "Query CTRL port policy");
  require(port.capability == SNDRV_SEQ_PORT_CAP_NO_EXPORT, "CTRL is private without sequencer IO");
  for (auto direction : {Direction::from_device, Direction::to_device}) {
    auto subs = subscription(seq, 1, direction);
    require(ioctl(seq.fd, SNDRV_SEQ_IOCTL_SUBSCRIBE_PORT, &subs) == -1 && errno == EPERM,
            "Bridge cannot subscribe to private CTRL in either direction");
  }
  auto din = subscription(seq, 0, Direction::to_device);
  checked(ioctl(seq.fd, SNDRV_SEQ_IOCTL_SUBSCRIBE_PORT, &din), "DIN MIDI remains bridgeable");
  check_raw_busy(0);
  check_raw_control_available();
  checked(ioctl(seq.fd, SNDRV_SEQ_IOCTL_UNSUBSCRIBE_PORT, &din), "Release DIN bridge");
  std::cout << "OCTA_CTRL_SEQ_PRIVATE_PASS\n";
}

} // namespace

int main() {
  guard();
  const auto seq = open_sequencer();
  private_policy(seq);
  checked(close(seq.fd), "Close sequencer bridge");
}
