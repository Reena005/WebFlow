import argparse
import struct
from collections import Counter, defaultdict
from pathlib import Path


PCAP_FORMATS = {
    bytes.fromhex("d4c3b2a1"): ("<", 6),
    bytes.fromhex("a1b2c3d4"): (">", 6),
    bytes.fromhex("4d3cb2a1"): ("<", 9),
    bytes.fromhex("a1b23c4d"): (">", 9),
}
LINK_ORDER = (
    "client-r1",
    "r1-r2",
    "r2-r5",
    "r1-r3",
    "r3-r4",
    "r4-r5",
    "r5-server",
)


def read_capture(path, client_ip, server_ip, udp_port, tcp_port):
    data = path.read_bytes()
    if len(data) < 24 or data[:4] not in PCAP_FORMATS:
        raise ValueError(f"Unsupported or invalid PCAP header: {path}")

    endian, fraction_width = PCAP_FORMATS[data[:4]]
    link_type = struct.unpack_from(endian + "I", data, 20)[0]
    if link_type != 9:
        raise ValueError(f"Expected PPP link type 9 in {path}, got {link_type}")

    counts = Counter()
    times = defaultdict(list)
    flows = defaultdict(Counter)
    offset = 24
    while offset < len(data):
        if offset + 16 > len(data):
            raise ValueError(f"Truncated packet record header: {path}")

        seconds, fraction, captured_length, _ = struct.unpack_from(
            endian + "IIII", data, offset
        )
        offset += 16
        frame = data[offset : offset + captured_length]
        if len(frame) != captured_length:
            raise ValueError(f"Truncated packet data: {path}")
        offset += captured_length
        counts["records"] += 1
        timestamp = f"{seconds}.{fraction:0{fraction_width}d}"

        if frame.startswith(bytes.fromhex("ff030021")):
            ip_offset = 4
        elif frame.startswith(bytes.fromhex("0021")):
            ip_offset = 2
        else:
            counts["unrelated"] += 1
            continue

        ip = frame[ip_offset:]
        if len(ip) < 20 or ip[0] >> 4 != 4:
            counts["unrelated"] += 1
            continue

        header_length = (ip[0] & 0x0F) * 4
        total_length = struct.unpack_from("!H", ip, 2)[0]
        if header_length < 20 or len(ip) < header_length or total_length < header_length:
            counts["unrelated"] += 1
            continue

        source_ip = ".".join(str(value) for value in ip[12:16])
        destination_ip = ".".join(str(value) for value in ip[16:20])
        if ip[9] == 17 and len(ip) >= header_length + 8:
            source_port, destination_port = struct.unpack_from("!HH", ip, header_length)
            flow = (source_ip, source_port, destination_ip, destination_port)
            if (
                source_ip == client_ip
                and destination_ip == server_ip
                and destination_port == udp_port
            ):
                kind = "udp_requests"
            elif (
                source_ip == server_ip
                and destination_ip == client_ip
                and source_port == udp_port
            ):
                kind = "udp_replies"
            else:
                counts["unrelated"] += 1
                continue

            counts[kind] += 1
            times[kind].append(timestamp)
            flows[kind][flow] += 1
        elif ip[9] == 6 and len(ip) >= header_length + 20:
            source_port, destination_port = struct.unpack_from("!HH", ip, header_length)
            tcp_header_length = (ip[header_length + 12] >> 4) * 4
            packet_length = min(total_length, len(ip))
            if tcp_header_length < 20 or packet_length < header_length + tcp_header_length:
                counts["unrelated"] += 1
                continue

            payload_length = packet_length - header_length - tcp_header_length
            flow = (source_ip, source_port, destination_ip, destination_port)
            if (
                source_ip == client_ip
                and destination_ip == server_ip
                and destination_port == tcp_port
            ):
                kind = "tcp_client_segments"
                payload_kind = "tcp_client_payload"
            elif (
                source_ip == server_ip
                and destination_ip == client_ip
                and source_port == tcp_port
            ):
                kind = "tcp_server_segments"
                payload_kind = "tcp_server_payload"
            else:
                counts["unrelated"] += 1
                continue

            counts[kind] += 1
            times[kind].append(timestamp)
            flows[kind][flow] += 1
            if payload_length:
                counts[payload_kind] += 1
                counts[payload_kind + "_bytes"] += payload_length
                times[payload_kind].append(timestamp)
                flows[payload_kind][flow] += 1
        else:
            counts["unrelated"] += 1

    return {"counts": counts, "times": times, "flows": flows}


def time_range(values):
    if not values:
        return "none"
    return f"{min(values)}..{max(values)}"


def format_flows(flows):
    if not flows:
        return "none"
    return "; ".join(
        f"{src}:{src_port} -> {dst}:{dst_port} ({count} packets)"
        for (src, src_port, dst, dst_port), count in sorted(flows.items())
    )


def main():
    parser = argparse.ArgumentParser(
        description="Verify WebFlow UDP/TCP forwarding paths from NS-3 PPP PCAP captures."
    )
    parser.add_argument("pcap_directory", type=Path)
    parser.add_argument("--client-ip", default="10.1.1.1")
    parser.add_argument("--server-ip", default="10.1.7.2")
    parser.add_argument("--server-port", type=int, default=9, help="UDP echo port")
    parser.add_argument("--tcp-port", type=int, default=8080)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    capture_paths = sorted(args.pcap_directory.glob("*.pcap"))
    if not capture_paths:
        parser.error(f"No PCAP files found in {args.pcap_directory}")

    captures = {}
    by_link = defaultdict(list)
    for path in capture_paths:
        link = path.stem.rsplit("-", 1)[0]
        capture = read_capture(
            path, args.client_ip, args.server_ip, args.server_port, args.tcp_port
        )
        captures[path] = capture
        by_link[link].append((path, capture))

    link_counts = {}
    for link in LINK_ORDER:
        combined = Counter()
        for _, capture in by_link.get(link, []):
            combined.update(capture["counts"])
        link_counts[link] = combined

    request_links = [
        link
        for link in LINK_ORDER
        if link_counts[link]["udp_requests"] or link_counts[link]["tcp_client_payload"]
    ]
    reply_links = [
        link
        for link in LINK_ORDER
        if link_counts[link]["udp_replies"] or link_counts[link]["tcp_server_payload"]
    ]
    branch_a = {"r1-r2", "r2-r5"}
    branch_b = {"r1-r3", "r3-r4", "r4-r5"}
    shared_links = {"client-r1", "r5-server"}
    required_a = branch_a | shared_links
    required_b = branch_b | shared_links
    a_seen = required_a.issubset(request_links) and required_a.issubset(reply_links)
    b_seen = required_b.issubset(request_links) and required_b.issubset(reply_links)

    lines = [
        "WebFlow NS-3 PCAP Route Verification",
        f"PCAP directory: {args.pcap_directory.resolve()}",
        f"Expected UDP echo: {args.client_ip}:ephemeral -> "
        f"{args.server_ip}:{args.server_port}, then reverse",
        f"Expected TCP flow: {args.client_ip}:ephemeral <-> "
        f"{args.server_ip}:{args.tcp_port}",
        f"Capture files: {len(captures)}",
        "Decoder: Python standard library; classic PCAP, PPP link type 9",
        "TCP payload counts are segment-level; TCP does not preserve application message boundaries.",
        "",
        "Per-link observations (counts sum both endpoint PCAPs; each packet is "
        "normally captured at both ends):",
    ]

    for link in LINK_ORDER:
        entries = by_link.get(link, [])
        counts = link_counts[link]
        if not counts["records"]:
            lines.append(f"{link}: EMPTY; {len(entries)} header-only capture(s)")
            continue

        lines.append(
            f"{link}: records={counts['records']}, "
            f"UDP-request/reply={counts['udp_requests']}/{counts['udp_replies']}, "
            f"TCP-segments-client/server={counts['tcp_client_segments']}/"
            f"{counts['tcp_server_segments']}, "
            f"TCP-payload-bytes-client/server={counts['tcp_client_payload_bytes']}/"
            f"{counts['tcp_server_payload_bytes']}, "
            f"unrelated={counts['unrelated']}"
        )
        for path, capture in entries:
            file_counts = capture["counts"]
            lines.append(
                f"  {path.name}: bytes={path.stat().st_size}, "
                f"records={file_counts['records']}, "
                f"UDP-times={time_range(capture['times']['udp_requests'])}/"
                f"{time_range(capture['times']['udp_replies'])}, "
                f"TCP-payload-times={time_range(capture['times']['tcp_client_payload'])}/"
                f"{time_range(capture['times']['tcp_server_payload'])}, "
                f"unrelated={file_counts['unrelated']}"
            )
            for kind in (
                "udp_requests",
                "udp_replies",
                "tcp_client_payload",
                "tcp_server_payload",
            ):
                if file_counts[kind]:
                    lines.append(f"    {kind} flow(s): {format_flows(capture['flows'][kind])}")

    lines.extend(("", f"Observed client-to-server data links: {', '.join(request_links) or 'none'}"))
    lines.append(f"Observed server-to-client data links: {', '.join(reply_links) or 'none'}")
    if a_seen and not b_seen:
        lines.append("Verified path: client -> R1 -> R2 -> R5 -> Server")
        lines.append("Verified return: Server -> R5 -> R2 -> R1 -> client")
    elif b_seen and not a_seen:
        lines.append("Verified path: client -> R1 -> R3 -> R4 -> R5 -> Server")
        lines.append("Verified return: Server -> R5 -> R4 -> R3 -> R1 -> client")
    elif a_seen and b_seen:
        lines.append("Both branch signatures are present; a single route is not established")
    else:
        lines.append("Route verdict: insufficient PCAP evidence for a complete branch")

    report = "\n".join(lines) + "\n"
    print(report, end="")
    if args.output:
        if args.output.exists():
            raise FileExistsError(f"Refusing to overwrite existing report: {args.output}")
        args.output.write_text(report, encoding="ascii")
        print(f"Saved report: {args.output.resolve()}")


if __name__ == "__main__":
    main()