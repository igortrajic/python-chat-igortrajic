import socket
import threading
import codecs
import argparse
import sys

def receive_messages(client_socket, decoder=None, buffer=""):
    """Listens for incoming messages from the server."""
    if decoder is None:
        decoder = codecs.getincrementaldecoder('utf-8')()
    while "\n" in buffer:
        message, buffer = buffer.split("\n", 1)
        if message:
            print(message)
    while True:
        try:
            data = client_socket.recv(1024)
            if not data:
                print("Connection closed by the server.")
                break
            buffer += decoder.decode(data)
            while "\n" in buffer:
                message, buffer = buffer.split("\n", 1)
                if message:
                    print(message)
        except ConnectionError:
            print("Connection to the server was lost.")
            break
        except Exception as e:
            print(f"Error receiving message: {e}")
            break

    client_socket.close()

def main():
    parser = argparse.ArgumentParser(description="TCP Chat Client")
    parser.add_argument("-H", "--host", type=str, default="localhost", help="Server host (default: localhost)")
    parser.add_argument("-p", "--port", type=int, default=12345, help="Server port (default: 12345)")
    args = parser.parse_args()

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((args.host, args.port))
    except (ConnectionRefusedError, socket.gaierror, TimeoutError, OSError) as e:
        print(f"Error: Could not connect to {args.host}:{args.port} ({e})")
        sys.exit(1)

    decoder = codecs.getincrementaldecoder('utf-8')()
    buffer = ""

    def recv_line():
        nonlocal buffer
        while "\n" not in buffer:
            data = client_socket.recv(1024)
            if not data:
                raise ConnectionError("Connection closed by the server.")
            buffer += decoder.decode(data)
        line, buffer = buffer.split("\n", 1)
        return line

    while True:
        username = ""
        while not username:
            try:
                username = input("Enter your username: ").strip()
            except (KeyboardInterrupt, EOFError):
                client_socket.close()
                sys.exit(0)
            if not username:
                print("Username cannot be empty.")

        try:
            client_socket.sendall((username + "\n").encode('utf-8'))
            response = recv_line()
        except (OSError, ConnectionError) as e:
            print(f"Error: Could not negotiate username ({e})")
            client_socket.close()
            sys.exit(1)

        if response == "USERNAME_OK":
            break
        elif response.startswith("USERNAME_TAKEN:"):
            print(response.split(":", 1)[1])
        else:
            break

    receive_thread = threading.Thread(target=receive_messages, args=(client_socket, decoder, buffer), daemon=True)
    receive_thread.start()

    try:
        while True:
            message = input()
            if not message:
                continue
            client_socket.sendall((message + "\n").encode('utf-8'))
    except (KeyboardInterrupt, EOFError, OSError):
        pass
    finally:
        client_socket.close()

if __name__ == "__main__":
    main()
