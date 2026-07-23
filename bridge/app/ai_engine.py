import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.memory_engine import MemoryEngine
from app.registry import RegistryEngine
from app.tool_engine import ToolEngine


logger = logging.getLogger("jarvis-core.ai")


class AIEngineError(RuntimeError):
    """Raised when the AI request cannot be completed safely."""


class AIEngine:
    def __init__(
        self,
        api_key: str,
        model: str,
        registry: RegistryEngine,
        tools: ToolEngine,
        memory: MemoryEngine,
    ) -> None:
        if not api_key.strip():
            raise AIEngineError(
                "OPENAI_API_KEY is not configured."
            )

        self.client = AsyncOpenAI(
            api_key=api_key,
        )
        self.model = model
        self.registry = registry
        self.tools = tools
        self.memory = memory

    async def _area_options(
        self,
    ) -> list[dict[str, str]]:
        areas = await self.registry.areas()

        return [
            {
                "area_id": area["area_id"],
                "name": area["name"],
            }
            for area in areas
            if area.get("area_id")
            and area.get("name")
        ]

    async def _openai_tools(
        self,
    ) -> list[dict[str, Any]]:
        areas = await self._area_options()
        area_ids = [
            area["area_id"]
            for area in areas
        ]

        descriptions = ", ".join(
            f'{area["name"]}={area["area_id"]}'
            for area in areas
        )

        devices = await self.tools.controllable_devices()

        device_entity_ids = [
            device["entity_id"]
            for device in devices
        ]

        device_descriptions = "; ".join(
            (
                f'{device["name"]}'
                f' ({device.get("area_name") or "No area"})'
                f'={device["entity_id"]}'
            )
            for device in devices
        )

        return [
            {
                "type": "function",
                "name": "control_area_lights",
                "description": (
                    "Turn all available lights in one Home Assistant "
                    "area on or off. Use the exact area_id. "
                    f"Available areas: {descriptions}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "area_id": {
                            "type": "string",
                            "enum": area_ids,
                        },
                        "action": {
                            "type": "string",
                            "enum": [
                                "turn_on",
                                "turn_off",
                            ],
                        },
                    },
                    "required": [
                        "area_id",
                        "action",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },

            {
                "type": "function",
                "name": "control_device",
                "description": (
                    "Turn one exact Home Assistant light or switch on "
                    "or off. Use this when Aaron names a specific device "
                    "such as the floodlight, LED ring, lamp or plug. "
                    "Do not use it for an entire room."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "enum": device_entity_ids,
                            "description": (
                                "The exact Home Assistant entity ID. "
                                f"Available devices: {device_descriptions}"
                            ),
                        },
                        "action": {
                            "type": "string",
                            "enum": [
                                "turn_on",
                                "turn_off",
                            ],
                        },
                    },
                    "required": [
                        "entity_id",
                        "action",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "save_memory",
                "description": (
                    "Save a durable fact that Aaron explicitly asks "
                    "Jarvis to remember. Do not save casual conversation, "
                    "temporary states, passwords, API keys, authentication "
                    "tokens, financial account details or other secrets."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "personal",
                                "preference",
                                "home",
                                "project",
                                "general",
                            ],
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "A short stable label for the fact."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "A clear factual sentence to remember."
                            ),
                        },
                    },
                    "required": [
                        "category",
                        "subject",
                        "content",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "forget_memory",
                "description": (
                    "Delete a saved memory only when Aaron explicitly "
                    "asks Jarvis to forget or remove it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "personal",
                                "preference",
                                "home",
                                "project",
                                "general",
                            ],
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "The exact stable subject label "
                                "for the memory."
                            ),
                        },
                    },
                    "required": [
                        "category",
                        "subject",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ]

    async def _area_name(
        self,
        area_id: str,
    ) -> str:
        for area in await self._area_options():
            if area["area_id"] == area_id:
                return area["name"]

        return area_id.replace(
            "_",
            " ",
        ).title()

    async def _execute_function(
        self,
        name: str,
        arguments_json: str,
    ) -> dict[str, Any]:
        try:
            arguments = json.loads(
                arguments_json
            )
        except json.JSONDecodeError as exc:
            raise AIEngineError(
                "OpenAI returned invalid tool arguments."
            ) from exc

        if name == "control_area_lights":
            area_id = arguments.get("area_id")
            action = arguments.get("action")

            valid_area_ids = {
                area["area_id"]
                for area in await self._area_options()
            }

            if area_id not in valid_area_ids:
                raise AIEngineError(
                    f"OpenAI requested an unknown area: {area_id}"
                )

            if action not in {
                "turn_on",
                "turn_off",
            }:
                raise AIEngineError(
                    f"OpenAI requested an invalid action: {action}"
                )

            result = await self.tools.control_area_lights(
                area_id=area_id,
                turn_on=action == "turn_on",
            )

            return {
                "tool": name,
                "arguments": arguments,
                "result": result,
            }


        if name == "control_device":
            entity_id = arguments.get(
                "entity_id",
                "",
            )
            action = arguments.get(
                "action",
                "",
            )

            valid_devices = {
                device["entity_id"]: device
                for device in await self.tools.controllable_devices()
            }

            if entity_id not in valid_devices:
                raise AIEngineError(
                    f"OpenAI requested an unknown or "
                    f"unavailable device: {entity_id}"
                )

            if action not in {
                "turn_on",
                "turn_off",
            }:
                raise AIEngineError(
                    f"OpenAI requested an invalid action: {action}"
                )

            result = await self.tools.control_device(
                entity_id=entity_id,
                turn_on=action == "turn_on",
            )

            return {
                "tool": name,
                "arguments": {
                    **arguments,
                    "area_id": result.get("area_id"),
                },
                "result": result,
            }

        if name == "save_memory":
            category = arguments.get(
                "category",
                "",
            )
            subject = arguments.get(
                "subject",
                "",
            )
            content = arguments.get(
                "content",
                "",
            )

            memory = await self.memory.save(
                category=category,
                subject=subject,
                content=content,
            )

            return {
                "tool": name,
                "arguments": arguments,
                "result": {
                    "success": True,
                    "memory": memory,
                },
            }

        if name == "forget_memory":
            category = arguments.get(
                "category",
                "",
            )
            subject = arguments.get(
                "subject",
                "",
            )

            deleted = await self.memory.delete(
                category=category,
                subject=subject,
            )

            return {
                "tool": name,
                "arguments": arguments,
                "result": {
                    "success": deleted,
                    "deleted": deleted,
                },
            }

        raise AIEngineError(
            f"OpenAI requested an unsupported tool: {name}"
        )

    async def _tool_reply(
        self,
        call: dict[str, Any],
    ) -> str:
        name = call["tool"]
        arguments = call["arguments"]
        result = call["result"]

        if name == "control_area_lights":
            area_name = await self._area_name(
                arguments["area_id"]
            )

            action_text = (
                "turned on"
                if arguments["action"] == "turn_on"
                else "turned off"
            )

            entity_count = len(
                result.get(
                    "entities",
                    [],
                )
            )

            if result.get("success"):
                return (
                    f"I have {action_text} "
                    f"{entity_count} light"
                    f"{'' if entity_count == 1 else 's'} "
                    f"in the {area_name}."
                )

            return result.get(
                "message",
                "The light-control request could not be completed.",
            )


        if name == "control_device":
            action_text = (
                "turned on"
                if arguments["action"] == "turn_on"
                else "turned off"
            )

            device_name = result.get(
                "name",
                arguments["entity_id"],
            )

            if result.get("success"):
                return (
                    f"I have {action_text} "
                    f"the {device_name}."
                )

            return result.get(
                "message",
                "The device-control request could not be completed.",
            )

        if name == "save_memory":
            memory = result.get(
                "memory",
                {},
            )

            return (
                "I will remember that "
                f'{memory.get("content", "information")}'
            )

        if name == "forget_memory":
            if result.get("deleted"):
                return (
                    "I have removed that memory."
                )

            return (
                "I could not find a matching saved memory to remove."
            )

        return "The request was completed."

    async def ask(
        self,
        text: str,
    ) -> dict[str, Any]:
        if not text or not text.strip():
            raise AIEngineError(
                "The request cannot be empty."
            )

        relevant_memory = await self.memory.context_for(
            query=text,
            limit=6,
        )

        input_messages: list[dict[str, str]] = []

        if relevant_memory:
            input_messages.append(
                {
                    "role": "developer",
                    "content": (
                        "Use the following saved memory only when it is "
                        "relevant to Aaron's request. Treat it as factual "
                        "user-provided context. Do not mention the memory "
                        "system unless asked.\n\n"
                        f"{relevant_memory}"
                    ),
                }
            )

        input_messages.append(
            {
                "role": "user",
                "content": text,
            }
        )

        try:
            response = await self.client.responses.create(
                model=self.model,
                instructions=(
                    "You are Jarvis, Aaron's Home Assistant controller. "
                    "Use British English. Be concise and professional. "
                    "For light-control requests, call the appropriate "
                    "tool. Only save memory when Aaron clearly and "
                    "explicitly asks you to remember something. "
                    "Only delete memory when Aaron clearly asks you to "
                    "forget or remove it. Never save passwords, API keys, "
                    "tokens, payment details or authentication secrets. "
                    "Never claim a physical action succeeded unless a "
                    "tool was called. Do not invent rooms, devices, "
                    "states, memories or capabilities."
                ),
                input=input_messages,
                tools=await self._openai_tools(),
                tool_choice="auto",
            )
        except Exception as exc:
            logger.exception(
                "OpenAI Responses API request failed"
            )

            raise AIEngineError(
                f"OpenAI request failed: {exc}"
            ) from exc

        function_calls = [
            item
            for item in response.output
            if getattr(
                item,
                "type",
                None,
            ) == "function_call"
        ]

        if not function_calls:
            reply = (
                response.output_text or ""
            ).strip()

            if not reply:
                reply = (
                    "I could not determine an appropriate response."
                )

            return {
                "success": True,
                "response": reply,
                "model": self.model,
                "tool_called": False,
                "calls": [],
                "memory_used": bool(
                    relevant_memory
                ),
            }

        completed_calls: list[
            dict[str, Any]
        ] = []

        for function_call in function_calls[:3]:
            completed_calls.append(
                await self._execute_function(
                    name=function_call.name,
                    arguments_json=function_call.arguments,
                )
            )

        success = all(
            call["result"].get(
                "success",
                False,
            )
            for call in completed_calls
        )

        if len(completed_calls) == 1:
            reply = await self._tool_reply(
                completed_calls[0]
            )
        else:
            successful_count = sum(
                1
                for call in completed_calls
                if call["result"].get(
                    "success"
                )
            )

            reply = (
                f"Completed {successful_count} of "
                f"{len(completed_calls)} requested actions."
            )

        return {
            "success": success,
            "response": reply,
            "model": self.model,
            "tool_called": True,
            "calls": completed_calls,
            "memory_used": bool(
                relevant_memory
            ),
        }
