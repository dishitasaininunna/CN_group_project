import socket
import time

server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.bind(("0.0.0.0", 5003))
server_socket.settimeout(0.2)

received_packets = set()
original_packets = set()
retransmitted_packets = set()
missing_packets = set()
last_nack_time = {}

total_packets = None
initial_missing_packets = None
client_address = None
end_time = None
complete_time = None
finished = False
recovery_deadline = None

print("Measured SDN-UDP server listening on port 5003", flush=True)

while not finished:
    now = time.monotonic()

    try:
        data, address = server_socket.recvfrom(1024)
        fields = data.decode().strip().split("|")
        client_address = address

        if fields[0] == "DATA" and len(fields) >= 4:
            sequence_number = int(fields[1])
            packet_type = fields[2]

            if total_packets is not None and not (
                1 <= sequence_number <= total_packets
            ):
                continue

            is_new = sequence_number not in received_packets

            if is_new:
                received_packets.add(sequence_number)

                if packet_type == "ORIGINAL":
                    original_packets.add(sequence_number)
                    print(
                        f"Received original packet {sequence_number}",
                        flush=True
                    )
                elif packet_type == "RETRANSMISSION":
                    retransmitted_packets.add(sequence_number)
                    print(
                        f"Received retransmitted packet {sequence_number}",
                        flush=True
                    )

                missing_packets.discard(sequence_number)

                if (
                    initial_missing_packets is not None
                    and not missing_packets
                    and complete_time is None
                ):
                    complete_time = time.monotonic()

            if (
                is_new
                and packet_type == "RETRANSMISSION"
                and initial_missing_packets is not None
                and sequence_number in initial_missing_packets
            ):
                ack_type = "RETRANSMISSION"
            elif is_new and packet_type == "ORIGINAL":
                ack_type = "ORIGINAL"
            else:
                ack_type = "DUPLICATE"

            server_socket.sendto(
                f"ACK|{sequence_number}|{ack_type}".encode(),
                address
            )

        elif fields[0] == "END" and len(fields) == 2:
            announced_total = int(fields[1])

            if announced_total <= 0:
                continue

            if total_packets is None:
                total_packets = announced_total
                end_time = time.monotonic()
                recovery_deadline = end_time + 20

                initial_missing_packets = (
                    set(range(1, total_packets + 1)) - received_packets
                )
                missing_packets = set(initial_missing_packets)

                print(
                    f"End marker received for {total_packets} packets",
                    flush=True
                )
                print(
                    f"Initially missing: {sorted(initial_missing_packets)}",
                    flush=True
                )

                if not missing_packets:
                    complete_time = time.monotonic()

            if announced_total != total_packets:
                continue

    except socket.timeout:
        pass
    except (ValueError, UnicodeDecodeError):
        continue

    now = time.monotonic()

    if total_packets is not None:
        missing_packets = (
            set(range(1, total_packets + 1)) - received_packets
        )

        if missing_packets and now < recovery_deadline:
            for number in sorted(missing_packets):
                if now - last_nack_time.get(number, 0) >= 0.5:
                    server_socket.sendto(
                        f"NACK|{number}".encode(), client_address
                    )
                    last_nack_time[number] = now

                    print(
                        f"Packet loss detected: {number}; "
                        f"requested retransmission",
                        flush=True
                    )

        final_missing = (
            set(range(1, total_packets + 1)) - received_packets
        )

        if not final_missing and complete_time is None:
            complete_time = now

        deadline_reached = now >= recovery_deadline
        ready_to_finish = (
            not final_missing
            and complete_time is not None
            and now - complete_time >= 2
        )

        if deadline_reached or ready_to_finish:
            initial_count = len(initial_missing_packets)
            final_count = len(final_missing)
            initial_loss = initial_count / total_packets * 100
            final_loss = final_count / total_packets * 100

            summary = (
                f"ENDACK|{total_packets}|{initial_count}|{final_count}"
            ).encode()

            for _ in range(5):
                server_socket.sendto(summary, client_address)
                time.sleep(0.05)

            print("\n========== SERVER SUMMARY ==========", flush=True)
            print(f"Total numbered packets: {total_packets}", flush=True)
            print(
                f"Initially missing at first END: "
                f"{initial_count}/{total_packets}",
                flush=True
            )
            print(f"Initial loss percentage: {initial_loss:.2f}%", flush=True)
            print(
                f"Recovered by retransmission: "
                f"{len(retransmitted_packets)}",
                flush=True
            )
            print(f"Final missing packets: {final_count}", flush=True)
            print(f"Final loss percentage: {final_loss:.2f}%", flush=True)
            print(
                f"Unrecovered sequence numbers: {sorted(final_missing)}",
                flush=True
            )
            if deadline_reached and final_missing:
                print("Recovery deadline reached.", flush=True)
            else:
                print("All packets were received.", flush=True)

            finished = True

server_socket.close()
print("Measured UDP server finished", flush=True)
