#!/usr/bin/env python3
import argparse
import sys

import serial
import serial.tools.list_ports

BAUD = 115200


def find_port():
    for p in serial.tools.list_ports.comports():
        text = ((p.description or '') + ' ' + (p.manufacturer or '')).lower()
        if 'stlink' in text or 'st-link' in text or 'stmicroelectronics' in text:
            return p.device
    return None


def comment(text):
    f = text.split()
    if len(f) < 3 or f[0] != 'R':
        return ''
    try:
        d = dict(zip(f[1::2], (int(x) for x in f[2::2])))
        lost = d['lost']
    except (ValueError, KeyError):
        return ''
    if lost == 0:
        return '   -> no update is lost'
    return '   -> %d updates are lost' % lost


def main():
    ap = argparse.ArgumentParser(description='Lab 3, Task B1: the mutex.')
    ap.add_argument('--port', help='COM port of the board')
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        sys.exit('No ST-LINK virtual COM port found. Use --port COMn.')

    ser = serial.Serial(port, BAUD, timeout=0.2)
    print('Reading %s. Press Ctrl+C to stop.' % port)
    buf = b''
    try:
        while True:
            chunk = ser.read(64)
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                text = raw.decode('ascii', errors='replace').strip()
                if text:
                    print('[board] ' + text + comment(text))
    except KeyboardInterrupt:
        print()
    finally:
        ser.close()


if __name__ == '__main__':
    main()
