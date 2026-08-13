import socket

TARGET = ("239.255.19.92", 1992)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

while True:
    value = input("Speed: ")
    sock.sendto(value.encode(), TARGET)
