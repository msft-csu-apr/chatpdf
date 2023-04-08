import logging, json, os, urllib
import azure.functions as func
import openai
from langchain.llms.openai import AzureOpenAI
import os
from langchain.agents import create_sql_agent
from langchain.agents.agent_toolkits import SQLDatabaseToolkit
from langchain.sql_database import SQLDatabase

OpenAiKey = os.environ['OpenAiKey']
OpenAiEndPoint = os.environ['OpenAiEndPoint']
OpenAiVersion = os.environ['OpenAiVersion']
OpenAiDavinci = os.environ['OpenAiDavinci']
OpenAiEmbedding = os.environ['OpenAiEmbedding']
OpenAiService = os.environ['OpenAiService']
OpenAiDocStorName = os.environ['OpenAiDocStorName']
OpenAiDocStorKey = os.environ['OpenAiDocStorKey']
OpenAiDocConnStr = f"DefaultEndpointsProtocol=https;AccountName={OpenAiDocStorName};AccountKey={OpenAiDocStorKey};EndpointSuffix=core.windows.net"
OpenAiDocContainer = os.environ['OpenAiDocContainer']
SynapseName = os.environ['SynapseName']
SynapseUser = os.environ['SynapseUser']
SynapsePassword = os.environ['SynapsePassword']
SynapsePool = os.environ['SynapsePool']

def FindSqlAnswer(topK, question, value):
    logging.info("Calling FindSqlAnswer Open AI")
    answer = ''
    os.environ['OPENAI_API_KEY'] = OpenAiKey

    try:
        synapseConnectionString = "Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{};" \
                      "Database={};Uid={};Pwd={};Encrypt=yes;TrustServerCertificate=no;" \
                      "Connection Timeout=30;".format(SynapseName, SynapsePool, SynapseUser, SynapsePassword)
        # synapseConnectionString = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:dataaisqlsrv.database.windows.net,1433;"\
        #     "Database=dataaisql;Uid=azureadmin;Pwd=P2ssw0rd2903$;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
        params = urllib.parse.quote_plus(synapseConnectionString)
        sqlConnectionString = 'mssql+pyodbc:///?odbc_connect={}'.format(params)
        db = SQLDatabase.from_uri(sqlConnectionString)
        #Unless the user specifies a specific number of examples they wish to obtain, always limit your query to at most {top_k} results using SELECT TOP in SQL Server syntax.

        SqlPrefix = """You are an agent designed to interact with SQL database systems.
        Given an input question, create a syntactically correct {dialect} query to run, then look at the results of the query and return the answer.
        Unless the user specifies a specific number of examples they wish to obtain, always limit your query to at most {top_k} results using SELECT TOP in SQL Server syntax.
        You can order the results by a relevant column to return the most interesting examples in the database.
        Never query for all the columns from a specific table, only ask for a the few relevant columns given the question.
        You have access to tools for interacting with the database.
        Only use the below tools. Only use the information returned by the below tools to construct your final answer.
        You MUST double check your query before executing it. If you get an error while executing a query, rewrite the query and try again.
        
        DO NOT make any DML statements (INSERT, UPDATE, DELETE, DROP etc.) to the database.
        
        If the question does not seem related to the database, just return "I don't know" as the answer.        
        """ 

        SqlSuffix = """Begin!
            Question: {input}
            Thought: I should look at the tables in the database to see what I can query.
            {agent_scratchpad}"""
        
        openai.api_type = "azure"
        openai.api_key = OpenAiKey
        openai.api_version = OpenAiVersion
        openai.api_base = f"https://{OpenAiService}.openai.azure.com"


        logging.info("LLM Setup done")

        toolkit = SQLDatabaseToolkit(db=db)
        logging.info("Toolkit Setup done")

        llm = AzureOpenAI(deployment_name=OpenAiDavinci,
                temperature=os.environ['Temperature'] or 0,
                openai_api_key=OpenAiKey)


        agentExecutor = create_sql_agent(
                llm=llm,
                toolkit=toolkit,
                verbose=True,
                prefix=SqlPrefix, 
                #suffix=SqlSuffix,
                top_k=topK,
                kwargs={"return_intermediate_steps": True}
            )
        agentExecutor.return_intermediate_steps = True
     
        logging.info("Agent Setup done")
        answer = agentExecutor._call({"input":question})
        return {"data_points": [], "answer": answer['output'], "thoughts": answer['intermediate_steps'], "error": ""}
    except Exception as e:
      logging.info("Error in FindSqlAnswer Open AI : " + str(e))

    #return answer

def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    logging.info(f'{context.function_name} HTTP trigger function processed a request.')
    if hasattr(context, 'retry_context'):
        logging.info(f'Current retry count: {context.retry_context.retry_count}')

        if context.retry_context.retry_count == context.retry_context.max_retry_count:
            logging.info(
                f"Max retries of {context.retry_context.max_retry_count} for "
                f"function {context.function_name} has been reached")

    try:
        topK = req.params.get('topK')
        question = req.params.get('question')
        logging.info("Input parameters : " + topK + " " + question)
        body = json.dumps(req.get_json())
    except ValueError:
        return func.HttpResponse(
             "Invalid body",
             status_code=400
        )

    if body:
        result = ComposeResponse(topK, question, body)
        return func.HttpResponse(result, mimetype="application/json")
    else:
        return func.HttpResponse(
             "Invalid body",
             status_code=400
        )

def ComposeResponse(topK, question, jsonData):
    values = json.loads(jsonData)['values']

    logging.info("Calling Compose Response")
    # Prepare the Output before the loop
    results = {}
    results["values"] = []

    for value in values:
        outputRecord = TransformValue(topK, question, value)
        if outputRecord != None:
            results["values"].append(outputRecord)
    return json.dumps(results, ensure_ascii=False)

def TransformValue(topK, question, record):
    logging.info("Calling Transform Value")
    try:
        recordId = record['recordId']
    except AssertionError  as error:
        return None

    # Validate the inputs
    try:
        assert ('data' in record), "'data' field is required."
        data = record['data']
        assert ('text' in data), "'text' field is required in 'data' object."

    except KeyError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "KeyError:" + error.args[0] }   ]
            })
    except AssertionError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "AssertionError:" + error.args[0] }   ]
            })
    except SystemError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "SystemError:" + error.args[0] }   ]
            })

    try:
        # Getting the items from the values/data/text
        value = data['text']

        answer = FindSqlAnswer(topK, question, value)
        return ({
            "recordId": recordId,
            "data": answer
            })

    except:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "Could not complete operation for record." }   ]
            })