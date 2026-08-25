import socket

HOST = "10.19.92.200"
PORT = 1992

s = socket.create_connection((HOST, PORT), timeout=5)
s.settimeout(2)

print(f"Connected to {HOST}:{PORT}")

while True:
    text = input("Send: ")
    data = (text + "\r\n").encode()

    s.sendall(data)
    print(f"TX: {data!r}")

    try:
        reply = s.recv(4096)
        print(f"RX: {reply!r}")
    except socket.timeout:
        print("RX: *** TIMEOUT ***")