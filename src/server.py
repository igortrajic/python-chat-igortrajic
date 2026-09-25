import socket
import threading
from rich.console import Console
import argparse

console = Console()

def handle_client(client_socket, address):
    console.print(f"[bold green]New connection established from {address}[/bold green]")
    try:
        while True: 
            message = client_socket.recv(1024).decode('utf-8')
            if not message :
                break
            console.print(f"[{address} {message}]")
    
    finally:
        client_socket.close()
        console.print(f"[bold yellow]Connection from {address} closed.[/bold yellow]")

def main():

    parser = argparse.ArgumentParser(description="TCP Chat Server")
    parser.add_argument("-p", "--port", type=int, default=12345, help="Port to listen on (default: 12345)")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        server.bind(('0.0.0.0', args.port))
        server.listen(5)
        console.print(f"[bold blue]Server listening on 0.0.0.0:{args.port}...[/bold blue]")
        
        while True:
            client_socket, address = server.accept()
            client_thread = threading.Thread(
                target=handle_client, 
                args=(client_socket, address)
            )
            client_thread.start()
            
    except KeyboardInterrupt:
        console.print("\n[bold red]Shutdown signal received. Shutting down gracefully...[/bold red]")
    finally:
        server.close()
        console.print("[bold red]Server closed.[/bold red]")

if __name__ == "__main__":
    main()
