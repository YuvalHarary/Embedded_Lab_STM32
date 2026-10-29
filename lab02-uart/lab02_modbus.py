#!/usr/bin/env python3
"""Lab 2, Part B: a Modbus RTU master for the board.

The board is a Modbus RTU slave with the address 1 and five holding
registers (Part B of the task document gives the register map):
    0  LD2: 1 on, 0 off (read and write)
    1  messages with a correct CRC (read)
    2  messages with a wrong CRC (read)
    3  clock cycles of the CRC in software (read)
    4  clock cycles of the CRC in the CRC unit (read)

On each round, the program writes 1 or 0 to register 0 (function 0x06),
then reads registers 0 to 4 (function 0x03). The line is noisy: with
--errors P, the program changes one bit in a share P of the requests that
it sends. The board does not reply to a message with a wrong CRC. After
0.2 s with no reply, the program sends the request again, for at most
5 send attempts. Replies are checked against the pending request.
The program exits with status 1 if any request fails; exception replies
are expected only with --exceptions.

    python -m pip install pyserial
    python lab02_modbus.py                  20 rounds, no errors
    python lab02_modbus.py --errors 0.3     one wrong bit in 30 % of the sends
    python lab02_modbus.py --write-only     function 0x06 only (Task B1)
    python lab02_modbus.py --exceptions     two requests that the board must refuse
    python lab02_modbus.py --rounds 100     100 rounds
    python lab02_modbus.py --seed 7         the same errors on each run
    python lab02_modbus.py --port COM5      if it does not find the port

Only one program can open the port. Stop the Serial Monitor first.
"""
import argparse
import random
import sys
import time

BAUD = 115200
SLAVE = 1
NREGS = 5
NAMES = ['LD2', 'good', 'bad', 'crc_sw', 'crc_hw']
TIMEOUT = 0.2          # s without a reply: send the request again
MAX_SENDS = 5          # sends of one request, at most
GAP = 10 * 3.5 / BAUD  # this lab's 8N1 format: 10 bits per character


def crc16(data):
    """CRC-16/MODBUS: polynomial 0xA001 (0x8005 reflected), start 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(body):
    c = crc16(body)
    return bytes(body) + bytes([c & 0xFF, c >> 8])     # low byte first


def crc_ok(message):
    return len(message) >= 4 and crc16(message[:-2]) == message[-2] | (message[-1] << 8)


def request(function, a, b):
    """A request with two 16-bit fields, high byte first."""
    return with_crc([SLAVE, function, a >> 8, a & 0xFF, b >> 8, b & 0xFF])


def add_noise(message, rng, p):
    """Change one bit in a share p of the requests, as a noisy line does."""
    data = bytearray(message)
    if rng.random() < p:
        bit = rng.randrange(len(data) * 8)
        data[bit // 8] ^= 1 << (bit % 8)
        return bytes(data), 1
    return bytes(data), 0


def read_reply(port):
    """The bytes of one reply: they end with a silence. b'' if none."""
    buf = bytearray()
    start = last = time.monotonic()
    while True:
        chunk = port.read(64)
        now = time.monotonic()
        if chunk:
            buf += chunk
            last = now
        elif buf and now - last > max(GAP, 0.005):
            return bytes(buf)
        elif not buf and now - start > TIMEOUT:
            return b''


class Stats:
    def __init__(self):
        self.sends = self.errors = self.lost = self.bad_replies = 0
        self.failed = 0


def reply_matches(reply, message):
    """Check CRC, address, and response format against the pending request."""
    if len(message) != 8 or not crc_ok(reply) or reply[0] != message[0]:
        return False
    function = message[1]
    if reply[1] == function | 0x80:
        return len(reply) == 5
    if reply[1] != function:
        return False
    if function == 0x06:
        return reply == message
    if function == 0x03:
        count = 2 * (message[4] << 8 | message[5])
        return len(reply) == count + 5 and reply[2] == count
    return False


def transaction(port, message, rng, p, stats):
    """Send a request until a matching normal or exception reply arrives.
    Returns (reply, sends); reply is b'' after MAX_SENDS sends."""
    for sends in range(1, MAX_SENDS + 1):
        data, err = add_noise(message, rng, p)
        stats.sends += 1
        stats.errors += err
        port.reset_input_buffer()
        port.write(data)
        port.flush()
        reply = read_reply(port)
        time.sleep(GAP)
        if not reply:
            continue                       # no reply: send it again
        if not reply_matches(reply, message):
            stats.bad_replies += 1
            continue
        return reply, sends
    stats.lost += 1
    return b'', MAX_SENDS


def describe(reply, message):
    if not reply:
        return 'no reply'
    if not reply_matches(reply, message):
        return 'invalid reply'
    function = message[1]
    if reply[1] == function | 0x80:
        return 'exception code %d' % reply[2]
    if function == 0x06:
        return 'OK'
    n = reply[2] // 2
    values = [reply[3 + 2 * i] << 8 | reply[4 + 2 * i] for i in range(n)]
    first = message[2] << 8 | message[3]
    names = [NAMES[first + i] if first + i < len(NAMES)
             else 'reg%d' % (first + i) for i in range(n)]
    return ' '.join('%s=%d' % (name, v) for name, v in zip(names, values))


def find_port():
    from serial.tools import list_ports
    st = [p.device for p in list_ports.comports() if p.vid == 0x0483]
    if len(st) == 1:
        return st[0]
    sys.exit('Cannot select the port of the board. Found: %s. '
             'Give it with --port, for example --port COM3.'
             % (', '.join(st) or 'none'))


def run(port, args, out=print):
    rng = random.Random(args.seed)
    stats = Stats()
    if args.exceptions:
        for text, message, function in (
                ('read register 9 (no such register)', request(0x03, 9, 1), 0x03),
                ('function 0x04 (not supported)', request(0x04, 0, 1), 0x04)):
            reply, sends = transaction(port, message, rng, 0.0, stats)
            if not reply or reply[1] != function | 0x80:
                stats.failed += 1
            out('[PC] %s: %s' % (text, describe(reply, message)))
        return stats
    for r in range(1, args.rounds + 1):
        value = r % 2
        message = request(0x06, 0, value)
        reply, sends = transaction(port, message, rng, args.errors, stats)
        if not reply or reply[1] != 0x06:
            stats.failed += 1
        line = '[PC] round %d: write LD2=%d: %s (%d send%s)' % (
            r, value, describe(reply, message), sends, '' if sends == 1 else 's')
        if not args.write_only:
            message = request(0x03, 0, NREGS)
            reply, sends = transaction(port, message, rng, args.errors, stats)
            if not reply or reply[1] != 0x03:
                stats.failed += 1
            line += '; read: %s (%d send%s)' % (
                describe(reply, message), sends, '' if sends == 1 else 's')
        out(line)
        time.sleep(0.25)
    out('[PC] done: %d sends, %d with a wrong bit, %d requests with no reply, '
        '%d invalid replies.' % (stats.sends, stats.errors,
                                stats.lost, stats.bad_replies))
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--port', help='serial port, for example COM3')
    ap.add_argument('--errors', type=float, default=0.0,
                    help='share of the sends with one wrong bit (0 to 1)')
    ap.add_argument('--rounds', type=int, default=20, help='rounds (20)')
    ap.add_argument('--write-only', action='store_true',
                    help='write register 0 only (function 0x06)')
    ap.add_argument('--exceptions', action='store_true',
                    help='send two requests that the board must refuse')
    ap.add_argument('--seed', type=int, help='repeat the same errors')
    args = ap.parse_args()
    try:
        import serial
    except ImportError:
        sys.exit('pyserial is not installed. Run: '
                 'python -m pip install pyserial')
    name = args.port or find_port()
    try:
        port = serial.Serial(name, BAUD, timeout=0.002)
    except serial.SerialException as e:
        sys.exit('Cannot open %s: %s\nClose the Serial Monitor '
                 'and any other program that uses the port.' % (name, e))
    print('[PC] %s open: Modbus RTU master, slave %d, %d baud, 8N1.'
          % (name, SLAVE, BAUD))
    try:
        stats = run(port, args)
        return 1 if stats.failed else 0
    except KeyboardInterrupt:
        print('[PC] stopped.')
        return 130
    finally:
        port.close()


if __name__ == '__main__':
    sys.exit(main())
