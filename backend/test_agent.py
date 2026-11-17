
import asyncio

from app.agent import (
    run_medicine_agent,
    create_evidence_tool,
    collect_evidence
)

async def main():
    sample_result = {
        "sources": [
            {
                "title": "Sample medicine reference",
                "url": "https://example.com/medicine",
                "content": "Paracetamol is an ingredient used for pain and fever relief."
            }
        ],
        "combination_sources": [],
        "ingredients": [
            {
                "name": "Paracetamol",
                "sources": [],
                "knowledge": {
                    "precautions": "People with liver disease should seek medical advice before using paracetamol."
                }
            }
        ]
    }

    # Step 1: Check collected evidence
    evidence = collect_evidence(sample_result)

    print("\nEVIDENCE COUNT:", len(evidence))
    print("\nEVIDENCE CONTENT:", evidence)

    # Step 2: Test the evidence tool directly
    evidence_tool = create_evidence_tool(evidence)

    print("\nDIRECT TOOL TEST:")
    print(evidence_tool.invoke({
        "query": "liver disease paracetamol"
    }))

    # Step 3: Run the AI agent
    result = await run_medicine_agent(
        medicine_name="Dolo 650",
        conditions=["Liver disease"],
        allergies=[],
        lookup_result=sample_result
    )

    print("\nPROVIDER:", result["provider"])
    print("\nAGENT ANSWER:\n")
    print(result["answer"])


asyncio.run(main())
