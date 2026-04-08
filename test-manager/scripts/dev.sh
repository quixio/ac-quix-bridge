#!/bin/bash

# Test Manager Local Development Script
# Unified script for managing the local Docker-based development environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Function to print colored messages
print_info() {
    echo -e "${BLUE}ℹ${NC}  $1"
}

print_success() {
    echo -e "${GREEN}✓${NC}  $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC}  $1"
}

print_error() {
    echo -e "${RED}✗${NC}  $1"
}

print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo ""
}

# Check if Docker is running
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker is not running. Please start Docker Desktop."
        exit 1
    fi
}

# Display usage
usage() {
    cat << EOF
Usage: ./scripts/dev.sh [COMMAND]

Commands:
    start       Start all services (MongoDB, InfluxDB, Config API, Backend, Frontend)
    stop        Stop all services
    restart     Restart all services
    rebuild     Rebuild and restart all services
    status      Show status of all services
    logs        Show logs for all services (or specify service name)
    logs-f      Follow logs for all services (or specify service name)
    shell       Open a bash shell in a service container
    clean       Stop services and remove volumes (WARNING: deletes all data)
    help        Show this help message

Examples:
    ./scripts/dev.sh start
    ./scripts/dev.sh logs backend
    ./scripts/dev.sh logs-f frontend
    ./scripts/dev.sh shell backend

Services:
    - mongodb       MongoDB database
    - influxdb      InfluxDB time-series database
    - config-api    Mock Configuration API
    - backend       FastAPI backend
    - frontend      Next.js frontend
    - config-form   Experiment Config Form (iframe)

EOF
}

# Start services
start() {
    print_header "Starting Local Development Environment"
    check_docker

    print_info "Starting all services with Docker Compose..."
    docker compose up -d

    echo ""
    print_success "All services started!"
    echo ""
    print_info "Waiting for services to become healthy..."
    sleep 3

    status

    echo ""
    print_success "Local development environment is ready!"
    echo ""
    print_info "Frontend:      http://localhost:3000"
    print_info "Backend:       http://localhost:8080"
    print_info "API Docs:      http://localhost:8080/"
    print_info "Config Form:   http://localhost:8002"
    echo ""
}

# Stop services
stop() {
    print_header "Stopping Local Development Environment"

    print_info "Stopping all services..."
    docker compose down

    print_success "All services stopped!"
}

# Restart services
restart() {
    print_header "Restarting Local Development Environment"
    check_docker

    print_info "Restarting all services..."
    docker compose restart

    echo ""
    print_success "All services restarted!"
    status
}

# Rebuild services
rebuild() {
    print_header "Rebuilding Local Development Environment"
    check_docker

    print_info "Stopping services..."
    docker compose down

    print_info "Rebuilding and starting services..."
    docker compose up -d --build

    echo ""
    print_success "All services rebuilt and started!"
    echo ""
    print_info "Waiting for services to become healthy..."
    sleep 3

    status
}

# Show status
status() {
    print_header "Service Status"
    docker compose ps

    echo ""
    print_info "Container Health:"
    docker compose ps --format "table {{.Service}}\t{{.Status}}" | grep -E "(Service|healthy|unhealthy|starting)" || true
}

# Show logs
logs() {
    local service="$1"
    if [ -z "$service" ]; then
        docker compose logs --tail=50
    else
        docker compose logs --tail=50 "$service"
    fi
}

# Follow logs
logs_follow() {
    local service="$1"
    if [ -z "$service" ]; then
        docker compose logs -f
    else
        docker compose logs -f "$service"
    fi
}

# Open shell in container
shell() {
    local service="$1"
    if [ -z "$service" ]; then
        print_error "Please specify a service name"
        echo "Available services: mongodb, influxdb, config-api, backend, frontend"
        exit 1
    fi

    print_info "Opening shell in $service container..."
    docker compose exec "$service" /bin/bash || docker compose exec "$service" /bin/sh
}

# Clean everything
clean() {
    print_header "Cleaning Local Development Environment"

    print_warning "This will stop all services and DELETE ALL DATA in volumes!"
    read -p "Are you sure? (yes/no): " confirm

    if [ "$confirm" != "yes" ]; then
        print_info "Cancelled."
        exit 0
    fi

    print_info "Stopping services..."
    docker compose down

    print_info "Removing volumes..."
    docker compose down -v

    print_success "Environment cleaned!"
}

# Main command handling
case "${1:-}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    rebuild)
        rebuild
        ;;
    status)
        status
        ;;
    logs)
        logs "${2:-}"
        ;;
    logs-f)
        logs_follow "${2:-}"
        ;;
    shell)
        shell "${2:-}"
        ;;
    clean)
        clean
        ;;
    help|--help|-h)
        usage
        ;;
    "")
        print_error "No command specified"
        echo ""
        usage
        exit 1
        ;;
    *)
        print_error "Unknown command: $1"
        echo ""
        usage
        exit 1
        ;;
esac
