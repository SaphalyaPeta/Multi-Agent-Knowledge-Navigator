import asyncio
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import ollama
import uuid
from mcp.client.streamable_http import streamablehttp_client


class OllamaMCPClient:
    def __init__(self, model):
        self.model = model

        # chat history
        self.messages = []  

        # MCP protocol-specific variables: session, exit_stack
        self.session: ClientSession | None = None
        self.exit_stack = AsyncExitStack()

    # connecting the mcp client with the server
    async def connect(self, server_path: str):
        # 1. set up the connection param for the server
        # server_params = StdioServerParameters(
        #     command="python3",  # change it to python if your environement supoorts it
        #     args=[server_script_path],
        #     env=None,
        # )

        # 2. create the transport layer between the client and the server
        # stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        transport = await self.exit_stack.enter_async_context(
            streamablehttp_client(url=server_path)
        )

        # 3. create input and output from the transport layer
        self.transport_output, self.transport_input, _ = transport


        #  4. based on the input and output, create a session
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.transport_output, self.transport_input))

        # 5. finally, initialize the session
        await self.session.initialize()
        print("Successfully connected and initialized session")

        # 6. query the list of tools avaialable in the server
        response = await self.session.list_tools()
                
        # 7. iterate over avaiable tools
        self.available_tools = []
        for tool in response.tools:
            # the descriptor of a tool expected by ollama
            tool_descriptor = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }                        
            self.available_tools.append(tool_descriptor)

        
        # 9. debug: print the tool names
        # print(self.available_tools)
        for tool in self.available_tools:
            print(f"Tool name: {tool['function']['name']}")

    async def communicate(self):
        print("\nOllama Agentic MCP Client Started!")
        print("Type a natural query or bash command (type 'quit' to exit).\n")

        # booting the mcp server terminal
        result = await self.session.call_tool("initiate_terminal", {"cwd": ""})
        print(result.content[0].text)
        
        self.messages.append({
            "role": "system",
            "content": "You are an AI assistant that can use terminal tools. You can access file system using the terminal."
                       "You can use the tools to execute bash commands. Please decide if you should use tools based on users request."
                       "Use the tools when necessary. Maintain context from prior conversation."
                       "- When you want to run a tool, ALWAYS include its exact name."
                       "- The available tool names from tool description provided."
                       "- Do not leave the function name blank."
                       "- Do not invent new tool names."
                       "- Please use tool to run any command. Please always give tool call as tool_calls object"
                       "- Whenever you receive a tool result, you must always explain it back to the user in natural language."
                       "- After every tool result, you must always respond to the user with a clear explanation."
                       "- When you get the response from the tool:"
                            "- Explain the result to the user in a clear, human-readable way."
                            "- If the file or folder requested does not exist, clearly state that."
                            "- If listing a directory, summarize how many items exist and their names."
                            "- If showing file metadata, summarize size, date, and other key info."
                            "- If an exception  occured after calling a tool, summarize the error details in a clear, human-readable way."
        }) 
        # are: 'initiate_terminal', 'run_command', 'terminate_terminal'.


        while True:
            try:
                # take user input from the console/input
                user_query = input("\n$ ").strip()
                
                # sanity check: whether the user wants to exit
                if user_query.lower() in {"quit", "exit"}:
                    break

                # formating the message
                self.messages.append(
                    {
                        "role": "user", 
                        "content": user_query
                    }
                )

                agent_response = ollama.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=self.available_tools,
                    stream=True,
                )

                response_buffer = []
                tool_calls = []

                # print("\n#####MODEL THINKING#####")
                for chunk in agent_response:
                    if chunk.message.content:
                        print(chunk.message.content, end='', flush=True)
                        response_buffer.append(chunk.message.content)

                    if chunk.message.tool_calls:
                        tool_calls.extend(chunk.message.tool_calls)
                # print("\n#####END MODEL THINKING#####")

                # ollama-supported format
                response_log = {
                    "role": "assistant",
                    "content": "".join(response_buffer),
                    "tool_calls": tool_calls,
                }

                self.messages.append(response_log)

                # print(assistant_msg)

                # if response_log["content"] and not response_log.get("tool_calls"):
                #     # print("\n" + assistant_msg["content"])
                #     continue

                if "tool_calls" in response_log:
                    for tool in response_log["tool_calls"]:
                        tool_name = tool.function.name
                        tool_args = tool.function.arguments

                        print(f"Model requested tool: {tool_name} {tool_args}")

                        # actually calling the tool in mcp server
                        tool_result = await self.session.call_tool(tool_name, tool_args)

                        # print(tool_result.content[0].text)
                        tool_call_id = str(uuid.uuid4())

                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": (
                                f"The tool '{tool.function.name}' has finished executing.\n"
                                f"Raw output:\n{tool_result.content[0].text}\n\n"
                                "Now explain this result to the user in a clear, human-readable way."
                                "If an error or exception occurred in the result, summarize it."
                            )
                        })
                        
                        if tool_result.content[0].text.startswith("ERROR"):
                            print("Debug: " + tool_result.content[0].text)

                        # this is local call to format the output
                        agent_response = ollama.chat(
                            model=self.model,
                            messages=self.messages,                            
                            stream=True,
                        )

                        response_buffer = []                        

                        # print("\n#####MODEL FOLLOW-UP THINKING#####")
                        for chunk in agent_response:
                            if chunk.message.content:
                                print(chunk.message.content, end='', flush=True)
                                response_buffer.append(chunk.message.content)

                        print()
                        # print("\n#####END MODEL FOLLOW-UP THINKING#####")

                        followup_msg = {
                            "role": "assistant",
                            "content": "".join(response_buffer),
                        }                        

                        self.messages.append(followup_msg)

                        # if followup_msg["content"]:
                        #     print("\n" + followup_msg["content"])

            except Exception as e:
                print(f"\nError: {str(e)}")

        result = await self.session.call_tool("terminate_terminal")
        print(result.content[0].text)

    async def cleanup(self):
        await self.exit_stack.aclose()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py http://127.0.0.1:3000/mcp")
        sys.exit(1)

    client = OllamaMCPClient(model="llama3.2:3b")
    # client = OllamaMCPClient(model ="gpt-oss:20b")
    
    try:
        await client.connect(sys.argv[1])
        await client.communicate()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
