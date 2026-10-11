import { Component, type ReactNode } from "react";

// A render failure in one screen must never leave the owner with an empty page and no explanation.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="m-6 max-w-xl rounded-xl border border-err/60 bg-err-bg p-4 text-err-fg">
        <p className="font-semibold">This screen hit an error and stopped.</p>
        <p className="mt-1 text-sm">{this.state.error.message}</p>
        <button className="mt-3 rounded-lg border border-err/60 px-3 py-1.5 text-sm" onClick={() => { this.setState({ error: null }); window.location.reload(); }}>Reload</button>
      </div>
    );
  }
}
