"""
Agent Loop State Machine

A sophisticated event-driven agent loop that manages LLM interactions with built-in safety rails and recovery mechanisms.
Based on GSD Pi's agent-loop architecture.

Key features:
- Separates AgentMessage (internal) from Message (LLM-compatible) until dispatch boundary
- Implements consecutive tool error tracking to prevent unbounded retry loops
- Supports steering/follow-up message modes ("all" vs "one-at-a-time")
- Transform context before LLM conversion for pruning/injection
- Tool filtering immediately before provider calls
- Event streaming architecture for real-time monitoring
"""

import asyncio
from typing import Optional, List, Dict, Any, Callable, Union, Literal
from dataclasses import dataclass, field
from enum import Enum
import time
from datetime import datetime


class MessageRole(Enum):
    """Message roles"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_RESULT = "tool_result"


class QueueMode(Enum):
    """Message queue modes"""
    ALL = "all"  # Process all messages at once
    ONE_AT_A_TIME = "one_at_a_time"  # Process one message at a time


class ToolExecutionMode(Enum):
    """Tool execution modes"""
    PARALLEL = "parallel"  # Execute tools in parallel
    SEQUENTIAL = "sequential"  # Execute tools sequentially


@dataclass
class AgentMessage:
    """Internal agent message format"""
    role: MessageRole
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_result_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Message:
    """LLM-compatible message format"""
    role: str
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_result_id: Optional[str] = None


@dataclass
class AgentTool:
    """Tool definition"""
    name: str
    description: str
    parameters: Dict[str, Any]
    function: Callable
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvent:
    """Agent event for streaming"""
    event_type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class AgentState:
    """Mutable agent state"""
    system_prompt: str = ""
    model: str = "unknown"
    thinking_level: str = "off"
    messages: List[AgentMessage] = field(default_factory=list)
    tools: List[AgentTool] = field(default_factory=list)
    is_streaming: bool = False
    streaming_message: Optional[AgentMessage] = None
    pending_tool_calls: set = field(default_factory=set)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentLoopConfig:
    """Configuration for agent loop"""
    max_consecutive_tool_errors: int = 3
    max_retries: int = 5
    retry_delay_base: float = 1.0
    retry_delay_max: float = 32.0
    tool_execution_mode: ToolExecutionMode = ToolExecutionMode.PARALLEL
    steering_mode: QueueMode = QueueMode.ALL
    follow_up_mode: QueueMode = QueueMode.ONE_AT_A_TIME
    enable_transform_context: bool = True
    enable_tool_filtering: bool = True
    timeout_seconds: float = 300.0


@dataclass
class BeforeToolCallContext:
    """Context before tool call"""
    tool: AgentTool
    arguments: Dict[str, Any]
    state: AgentState
    call_id: str


@dataclass
class AfterToolCallContext:
    """Context after tool call"""
    tool: AgentTool
    arguments: Dict[str, Any]
    result: Any
    error: Optional[Exception]
    state: AgentState
    call_id: str
    duration_seconds: float


@dataclass
class BeforeToolCallResult:
    """Result of before tool call hook"""
    modified_arguments: Optional[Dict[str, Any]] = None
    skip_call: bool = False
    custom_result: Optional[Any] = None


@dataclass
class AfterToolCallResult:
    """Result of after tool call hook"""
    modified_result: Optional[Any] = None
    retry_call: bool = False
    additional_messages: Optional[List[AgentMessage]] = None


class AgentLoop:
    """
    Sophisticated event-driven agent loop with safety rails and recovery mechanisms.
    
    Key features:
    - Separates internal AgentMessage from external Message until dispatch
    - Consecutive tool error tracking prevents infinite retry loops
    - Flexible context transformation pipeline
    - Event streaming for real-time monitoring
    - Configurable safety rails and recovery policies
    """
    
    def __init__(
        self,
        config: Optional[AgentLoopConfig] = None,
        stream_fn: Optional[Callable] = None,
        before_tool_call: Optional[Callable[[BeforeToolCallContext], BeforeToolCallResult]] = None,
        after_tool_call: Optional[Callable[[AfterToolCallContext], AfterToolCallResult]] = None,
        transform_context: Optional[Callable[[List[AgentMessage]], List[AgentMessage]]] = None,
        event_callback: Optional[Callable[[AgentEvent], None]] = None
    ):
        self.config = config or AgentLoopConfig()
        self.stream_fn = stream_fn
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.transform_context = transform_context
        self.event_callback = event_callback
        
        self.state = AgentState()
        self._consecutive_tool_errors = 0
        self._is_running = False
        self._current_turn = 0
        
    def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """Emit an event if callback is registered"""
        if self.event_callback:
            event = AgentEvent(event_type=event_type, data=data)
            self.event_callback(event)
    
    def _convert_to_llm_messages(self, messages: List[AgentMessage]) -> List[Message]:
        """Convert internal AgentMessage to LLM-compatible Message"""
        return [
            Message(
                role=msg.role.value,
                content=msg.content,
                tool_calls=msg.tool_calls,
                tool_result_id=msg.tool_result_id
            )
            for msg in messages
            if msg.role in [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL_RESULT]
        ]
    
    def _apply_transform_context(self, messages: List[AgentMessage]) -> List[AgentMessage]:
        """Apply context transformation if enabled"""
        if self.config.enable_transform_context and self.transform_context:
            return self.transform_context(messages)
        return messages
    
    def _apply_tool_filtering(self, tools: List[AgentTool]) -> List[AgentTool]:
        """Apply tool filtering if enabled"""
        if self.config.enable_tool_filtering:
            # Filter tools based on current state/context
            # This is where you'd implement tool compatibility checks
            return tools
        return tools
    
    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay"""
        delay = self.config.retry_delay_base * (2 ** min(attempt, 10))
        return min(delay, self.config.retry_delay_max)
    
    async def _execute_tool_call(
        self,
        tool: AgentTool,
        arguments: Dict[str, Any],
        call_id: str
    ) -> tuple[Any, Optional[Exception]]:
        """Execute a single tool call with hooks"""
        start_time = time.time()
        
        # Before tool call hook
        if self.before_tool_call:
            context = BeforeToolCallContext(
                tool=tool,
                arguments=arguments,
                state=self.state,
                call_id=call_id
            )
            result = await self._run_hook(self.before_tool_call, context)
            
            if result.skip_call:
                if result.custom_result is not None:
                    return result.custom_result, None
                return None, None
            
            if result.modified_arguments:
                arguments = result.modified_arguments
        
        # Execute the tool
        try:
            if asyncio.iscoroutinefunction(tool.function):
                result = await tool.function(**arguments)
            else:
                result = tool.function(**arguments)
            error = None
        except Exception as e:
            result = None
            error = e
            self._consecutive_tool_errors += 1
        
        duration = time.time() - start_time
        
        # After tool call hook
        if self.after_tool_call:
            context = AfterToolCallContext(
                tool=tool,
                arguments=arguments,
                result=result,
                error=error,
                state=self.state,
                call_id=call_id,
                duration_seconds=duration
            )
            hook_result = await self._run_hook(self.after_tool_call, context)
            
            if hook_result.retry_call and error:
                # Signal to retry
                return None, error
            
            if hook_result.modified_result is not None:
                result = hook_result.modified_result
            
            if hook_result.additional_messages:
                self.state.messages.extend(hook_result.additional_messages)
        
        return result, error
    
    async def _run_hook(self, hook_func: Callable, context: Any) -> Any:
        """Run a hook function, handling both sync and async"""
        if asyncio.iscoroutinefunction(hook_func):
            return await hook_func(context)
        else:
            return hook_func(context)
    
    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Execute multiple tool calls based on configuration"""
        self._emit_event("tool_calls_start", {"count": len(tool_calls)})
        
        results = []
        
        if self.config.tool_execution_mode == ToolExecutionMode.PARALLEL:
            # Execute in parallel
            tasks = []
            for tool_call in tool_calls:
                tool = self._find_tool(tool_call["name"])
                if tool:
                    task = self._execute_tool_call(
                        tool,
                        tool_call.get("arguments", {}),
                        tool_call.get("id", "")
                    )
                    tasks.append(task)
            
            if tasks:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)
                for tool_call, task_result in zip(tool_calls, task_results):
                    if isinstance(task_result, Exception):
                        results.append({
                            "tool_call": tool_call,
                            "error": str(task_result),
                            "success": False
                        })
                    else:
                        result, error = task_result
                        results.append({
                            "tool_call": tool_call,
                            "result": result,
                            "error": str(error) if error else None,
                            "success": error is None
                        })
        else:
            # Execute sequentially
            for tool_call in tool_calls:
                tool = self._find_tool(tool_call["name"])
                if tool:
                    result, error = await self._execute_tool_call(
                        tool,
                        tool_call.get("arguments", {}),
                        tool_call.get("id", "")
                    )
                    results.append({
                        "tool_call": tool_call,
                        "result": result,
                        "error": str(error) if error else None,
                        "success": error is None
                    })
        
        self._emit_event("tool_calls_complete", {"results": results})
        return results
    
    def _find_tool(self, tool_name: str) -> Optional[AgentTool]:
        """Find a tool by name"""
        for tool in self.state.tools:
            if tool.name == tool_name:
                return tool
        return None
    
    def _check_safety_rails(self) -> bool:
        """Check if safety rails allow continuation"""
        # Check consecutive tool errors
        if self._consecutive_tool_errors >= self.config.max_consecutive_tool_errors:
            self.state.error_message = f"Too many consecutive tool errors: {self._consecutive_tool_errors}"
            self._emit_event("safety_rail_triggered", {
                "reason": "consecutive_tool_errors",
                "count": self._consecutive_tool_errors
            })
            return False
        
        return True
    
    async def _single_turn(self) -> bool:
        """Execute a single agent turn"""
        self._current_turn += 1
        self._emit_event("turn_start", {"turn": self._current_turn})
        
        # Check safety rails
        if not self._check_safety_rails():
            return False
        
        # Transform context
        messages = self._apply_transform_context(self.state.messages)
        
        # Convert to LLM format
        llm_messages = self._convert_to_llm_messages(messages)
        
        # Filter tools
        available_tools = self._apply_tool_filtering(self.state.tools)
        
        # Stream to LLM
        if self.stream_fn:
            self.state.is_streaming = True
            
            try:
                response = await asyncio.wait_for(
                    self.stream_fn(llm_messages, available_tools),
                    timeout=self.config.timeout_seconds
                )
            except asyncio.TimeoutError:
                self.state.error_message = "LLM request timed out"
                self._emit_event("timeout", {"turn": self._current_turn})
                return False
            
            self.state.is_streaming = False
            
            # Process response
            assistant_message = AgentMessage(
                role=MessageRole.ASSISTANT,
                content=response.get("content", ""),
                tool_calls=response.get("tool_calls", [])
            )
            
            self.state.messages.append(assistant_message)
            
            # Execute tool calls if present
            if assistant_message.tool_calls:
                tool_results = await self._execute_tool_calls(assistant_message.tool_calls)
                
                # Add tool results as messages
                for tool_result in tool_results:
                    result_message = AgentMessage(
                        role=MessageRole.TOOL_RESULT,
                        content=str(tool_result.get("result", "")),
                        tool_result_id=tool_result["tool_call"].get("id", ""),
                        metadata={"success": tool_result["success"]}
                    )
                    self.state.messages.append(result_message)
                
                # Reset consecutive errors if any tool succeeded
                if any(r["success"] for r in tool_results):
                    self._consecutive_tool_errors = 0
            
            self._emit_event("turn_complete", {"turn": self._current_turn})
            return True
        
        return False
    
    async def run(self, max_turns: int = 10) -> Dict[str, Any]:
        """
        Run the agent loop for a specified number of turns.
        
        Returns:
            Dict with execution summary
        """
        self._is_running = True
        self._emit_event("loop_start", {"max_turns": max_turns})
        
        summary = {
            "turns_completed": 0,
            "total_tool_calls": 0,
            "errors": [],
            "final_state": None
        }
        
        try:
            for turn in range(max_turns):
                if not self._is_running:
                    break
                
                success = await self._single_turn()
                
                if success:
                    summary["turns_completed"] += 1
                else:
                    # Break on error
                    if self.state.error_message:
                        summary["errors"].append(self.state.error_message)
                    break
                
                # Check if we should continue (no pending tool calls)
                if not self.state.messages[-1].tool_calls:
                    break
                    
        except Exception as e:
            summary["errors"].append(str(e))
            self._emit_event("loop_error", {"error": str(e)})
        
        self._is_running = False
        summary["final_state"] = self.state
        summary["total_tool_calls"] = sum(
            1 for msg in self.state.messages if msg.role == MessageRole.TOOL_RESULT
        )
        
        self._emit_event("loop_complete", summary)
        return summary
    
    def stop(self):
        """Stop the agent loop"""
        self._is_running = False
        self._emit_event("loop_stop", {})
    
    def add_message(self, role: MessageRole, content: str, **kwargs):
        """Add a message to the conversation"""
        message = AgentMessage(role=role, content=content, **kwargs)
        self.state.messages.append(message)
    
    def add_tool(self, tool: AgentTool):
        """Add a tool to the available tools"""
        self.state.tools.append(tool)
    
    def set_system_prompt(self, prompt: str):
        """Set the system prompt"""
        self.state.system_prompt = prompt
    
    def reset_conversation(self):
        """Reset the conversation while keeping tools and system prompt"""
        self.state.messages = []
        self._consecutive_tool_errors = 0
        self._current_turn = 0
        self.state.error_message = None
        self._emit_event("conversation_reset", {})