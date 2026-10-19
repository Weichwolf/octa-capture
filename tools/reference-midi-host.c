// SPDX-License-Identifier: GPL-2.0-only
// Serial host model for selected, hash-pinned ALSA MIDI functions. No USB/ALSA I/O.
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

enum {
  buffer_size = 65536,
  cable_count = 16,
  exposed_ports = 2,
  byte_bits = 8,
  note_on_cin = 9,
  note_on_status = 0x90,
  control_note_header = 0x19,
  first_note = 0x40,
  second_note = 0x41,
  full_velocity = 0x7f,
  HZ = 1000,
  GFP_ATOMIC = 1
};

struct urb {
  void *transfer_buffer;
  int transfer_buffer_length;
  int actual_length;
  int status;
  void *context;
  void *dev;
};

struct snd_usb_midi_in_endpoint;

struct usb_protocol_ops {
  void (*output_packet)(struct urb *, uint8_t, uint8_t, uint8_t, uint8_t);
  void (*input)(struct snd_usb_midi_in_endpoint *, uint8_t *, int);
};

struct snd_usb_midi {
  const struct usb_protocol_ops *usb_protocol_ops;
  unsigned long input_triggered;
  unsigned long error_timer;
  void *dev;
};

struct snd_usb_midi_out_endpoint {
  struct snd_usb_midi *umidi;
};

struct usbmidi_out_port {
  struct snd_usb_midi_out_endpoint *ep;
  uint8_t cable;
  uint8_t state;
  uint8_t data[2];
};

struct snd_rawmidi_substream {
  int number;
};

struct usbmidi_in_port {
  struct snd_rawmidi_substream *substream;
};

struct snd_usb_midi_in_endpoint {
  struct snd_usb_midi *umidi;
  struct usbmidi_in_port ports[cable_count];
  uint8_t error_resubmit;
};

static uint8_t received[exposed_ports][buffer_size];
static size_t received_size[exposed_ports];
static unsigned int submissions;
static unsigned int timers;
static int submit_result;
static const unsigned long jiffies = 1000;
typedef unsigned int gfp_t;

// Logging and atomic bit reads are host stubs; this model is deliberately serial.
#define dev_dbg(...) ((void)0)
#define dev_err(...) ((void)0)
#define dump_urb(...) ((void)0)

static bool test_bit(int bit, const unsigned long *mask) {
  return (*mask & (1UL << bit)) != 0;
}

static int usb_submit_urb(struct urb *urb, gfp_t flags) {
  if (urb == NULL || flags != GFP_ATOMIC) { abort(); }
  ++submissions;
  return submit_result;
}

static int mod_timer(unsigned long *timer, unsigned long deadline) {
  *timer = deadline;
  ++timers;
  return 0;
}

static int
snd_rawmidi_receive(struct snd_rawmidi_substream *stream, const uint8_t *data, int length) {
  if (stream->number < 0 || stream->number >= exposed_ports || length < 0 || length > 3) {
    abort();
  }
  const size_t size = (size_t)length;
  const size_t port = (size_t)stream->number;
  if (size > buffer_size - received_size[port]) { abort(); }
  for (size_t index = 0; index < size; ++index) {
    received[port][received_size[port] + index] = data[index];
  }
  received_size[port] += size;
  return length;
}

// The verifier generates this include from audited Linux sources, preserving notices.
#include "alsa-midi-reference.inc"

static bool read_word(uint32_t *value) {
  uint8_t bytes[4];
  if (fread(bytes, 1, sizeof(bytes), stdin) != sizeof(bytes)) { return false; }
  *value = 0;
  for (unsigned int index = 0; index < sizeof(bytes); ++index) {
    *value |= (uint32_t)bytes[index] << (index * byte_bits);
  }
  return true;
}

static bool write_bytes(const uint8_t *data, size_t size) {
  if (size > UINT32_MAX) { return false; }
  const uint32_t value = (uint32_t)size;
  uint8_t bytes[4];
  for (unsigned int index = 0; index < sizeof(bytes); ++index) {
    bytes[index] = (uint8_t)(value >> (index * byte_bits));
  }
  return fwrite(bytes, 1, sizeof(bytes), stdout) == sizeof(bytes) &&
         fwrite(data, 1, size, stdout) == size;
}

static bool transmit(const uint8_t *data, size_t size) {
  if (size % 2 != 0 || size > buffer_size / 2) { return false; }
  uint8_t output[buffer_size];
  struct urb urb = {.transfer_buffer = output, .transfer_buffer_length = 0};
  const struct usb_protocol_ops ops = {.output_packet = snd_usbmidi_output_standard_packet};
  struct snd_usb_midi device = {.usb_protocol_ops = &ops};
  struct snd_usb_midi_out_endpoint endpoint = {.umidi = &device};
  struct usbmidi_out_port ports[cable_count] = {0};
  for (size_t index = 0; index < cable_count; ++index) {
    ports[index].ep = &endpoint;
    ports[index].cable = (uint8_t)(index << 4);
  }
  for (size_t index = 0; index < size; index += 2) {
    if (data[index] >= cable_count) { return false; }
    snd_usbmidi_transmit_byte(&ports[data[index]], data[index + 1], &urb);
  }
  if (urb.transfer_buffer_length < 0) { return false; }
  return write_bytes(output, (size_t)urb.transfer_buffer_length);
}

static bool receive(int size, uint8_t *data, unsigned long triggered) {
  struct snd_usb_midi device = {.input_triggered = triggered};
  struct snd_rawmidi_substream streams[exposed_ports] = {{.number = 0}, {.number = 1}};
  struct snd_usb_midi_in_endpoint endpoint = {.umidi = &device};
  for (size_t port = 0; port < exposed_ports; ++port) {
    received_size[port] = 0;
    endpoint.ports[port].substream = &streams[port];
  }
  snd_usbmidi_standard_input(&endpoint, data, size);
  for (size_t port = 0; port < exposed_ports; ++port) {
    if (!write_bytes(received[port], received_size[port])) { return false; }
  }
  return true;
}

struct completion_request {
  int status;
  int submit;
};

static int signed_word(uint32_t value) {
  return value <= INT32_MAX ? (int)value : -1 - (int)(UINT32_MAX - value);
}

static bool completion(struct completion_request request) {
  uint8_t input[] = {note_on_cin,
                     note_on_status,
                     first_note,
                     full_velocity,
                     control_note_header,
                     note_on_status,
                     second_note,
                     full_velocity};
  const struct usb_protocol_ops ops = {.input = snd_usbmidi_standard_input};
  struct snd_usb_midi device = {.usb_protocol_ops = &ops, .input_triggered = 3};
  struct snd_rawmidi_substream streams[exposed_ports] = {{.number = 0}, {.number = 1}};
  struct snd_usb_midi_in_endpoint endpoint = {.umidi = &device};
  for (size_t port = 0; port < exposed_ports; ++port) {
    received_size[port] = 0;
    endpoint.ports[port].substream = &streams[port];
  }
  struct urb urb = {.transfer_buffer = input,
                    .actual_length = sizeof(input),
                    .status = request.status,
                    .context = &endpoint};
  submissions = 0;
  timers = 0;
  submit_result = request.submit;
  snd_usbmidi_in_urb_complete(&urb);
  const uint8_t result[] = {(uint8_t)submissions,
                            (uint8_t)timers,
                            endpoint.error_resubmit,
                            (uint8_t)received_size[0],
                            (uint8_t)received_size[1],
                            (uint8_t)(device.error_timer - (timers ? jiffies : 0))};
  return write_bytes(result, sizeof(result));
}

int main(void) {
  uint8_t input[buffer_size];
  int mode;
  while ((mode = getchar()) != EOF) {
    const int triggered = getchar();
    uint32_t size = 0;
    if (triggered == EOF || !read_word(&size) || size > sizeof(input) ||
        fread(input, 1, size, stdin) != size) {
      return EXIT_FAILURE;
    }
    if (mode == 0) {
      if (!transmit(input, size)) { return EXIT_FAILURE; }
    } else if (mode == 1) {
      if (!receive((int)size, input, (unsigned long)triggered)) { return EXIT_FAILURE; }
    } else if (mode == 2 && size == 8) {
      uint32_t status = 0;
      uint32_t submission = 0;
      for (unsigned int index = 0; index < 4; ++index) {
        status |= (uint32_t)input[index] << (index * 8);
        submission |= (uint32_t)input[index + 4] << (index * 8);
      }
      const struct completion_request request = {.status = signed_word(status),
                                                 .submit = signed_word(submission)};
      if (!completion(request)) { return EXIT_FAILURE; }
    } else {
      return EXIT_FAILURE;
    }
  }
  return ferror(stdin) || fflush(stdout) != 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}
