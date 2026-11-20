/** Incremental SSE parser; transport chunks are not event or UTF-8 boundaries. */
export class SSEDecoder {
  private decoder = new TextDecoder("utf-8", { fatal: true });
  private buffer = "";
  feed(bytes: Uint8Array, final = false): any[] {
    this.buffer += this.decoder.decode(bytes, { stream: !final });
    const frames = this.buffer.split(/\r?\n\r?\n/);
    this.buffer = frames.pop()!;
    if (this.buffer.length > 262144) throw new Error("Response frame is too large");
    if (final && this.buffer) { frames.push(this.buffer); this.buffer = ""; }
    return frames.flatMap(frame => {
      if (frame.length > 262144) throw new Error("Response frame is too large");
      const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
      if (!data || data === "[DONE]") return [];
      const value = JSON.parse(data);
      if (value.version !== 1) throw new Error("Unsupported stream version");
      return [value];
    });
  }
}
