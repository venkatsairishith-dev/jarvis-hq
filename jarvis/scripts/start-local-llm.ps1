# Start the local model server (laptop, Windows). Bound to localhost only; the kernel/gateway reaches it through the node, never directly.
# Flags learned on this machine (Radeon Pro 5500M, llama.cpp b11461 Vulkan): -fa off is REQUIRED (flash attention gives garbage
# output on this driver); -np 1 keeps speed; enable_thinking=false stops hidden reasoning tokens. Qwen3-1.7B Q4_K_M ~70-83 tokens/s.
param([string]$Model = 'D:\jarvis\models\gguf\Qwen3-1.7B-Q4_K_M.gguf', [int]$Port = 8080)
$dir = 'D:\jarvis\tools\llama.cpp\vulkan'
Start-Process -FilePath "$dir\llama-server.exe" -WorkingDirectory $dir -WindowStyle Hidden -ArgumentList @(
  '-m', $Model, '-ngl', '99', '-c', '4096', '-np', '1', '-fa', 'off', '--host', '127.0.0.1', '--port', $Port,
  '--jinja', '--chat-template-kwargs', '{\"enable_thinking\":false}')
