"""Container entrypoint; honor the port supplied by CDK."""
import os

import uvicorn

# Longer than the load balancer's 150-second idle timeout, so the ALB, not uvicorn, closes an idle
# keep-alive connection. Otherwise the ALB can reuse a connection uvicorn has just closed: a 502.
KEEP_ALIVE_SECONDS = 155
# ECS kills a task 30 seconds after SIGTERM by default. Finish in-flight responses before that;
# the target group's deregistration delay has already drained most of them.
GRACEFUL_SHUTDOWN_SECONDS = 25


def main() -> None:
    # Loopback by default. The container image sets HOST explicitly so the ALB can
    # reach the task; its security group only admits the load balancer.
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


if __name__ == "__main__":
    main()
