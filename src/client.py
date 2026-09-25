import socket
import threading
import argparse
import sys

def receive_messages(client_socket):
    """Listens for incoming messages from the server."""
    while True:
        try:
            message = client_socket.recv(1024).decode('utf-8')
            if not message:
                print("Connection closed by the server.")
                break
            print(message)
        except ConnectionError:
            print("Connection to the server was lost.")
            break
        except Exception:
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

    receive_thread = threading.Thread(target=receive_messages, args=(client_socket,), daemon=True)
    receive_thread.start()

    try:
        while True:
            message = input()
            if not message:
                continue
            client_socket.sendall(message.encode('utf-8'))
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        client_socket.close()

if __name__ == "__main__":
    main()
