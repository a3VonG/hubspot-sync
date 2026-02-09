- We need to be able to enrich leads, so let's say we get a company name  and or company domain or linked in. Classify if it is a dental lab based on browing the website, .. if it is a dental lab we want to would want to get a specific summary on the actual company so that we know which types of devices 
- We might get inputs from various places, google etc. but we would need to che ck them against our single source of truth which is hubspot, to know if they should be eplxored further etc. 
- Everything should end up in hubspot as a company with potential contacts etc. 
- If it is a dental lab we should find the contacts, linked in urls, emails etc. and add them to hubspot  
- The system should be maintainable and clear
- For finding leads  
- It would be great if it could be agent driven somehow meaning:
- We can build upon clay (we pay for it, but I would prefer the workflows to be driven from here)
- We can also use other APIs e.g. anthropic with the API Keys. 
- 



There are are few modes/ functionalities that need to be supported:

- Find potential leads (company driven)
    - end goal is to come up with a list of leads that should be given to finalize potential leads
    - This might start from a clear prompt on type of companies that it may find. 
    - We have access to google API for search result, of course agent would need to come up with various queries etc. 
    - Would be great if this could be agent driven e.g. their goal is to find a list of X leads, using method xyz. I don't know if you know good practices to manage agenets here. 
    - should do a quick very forgiving scan agent wise to know if this is a dental lab. 
    - Other option is that we give the agent a website of potential leads (e.g. a directory list and that it goes through them and extract the data)
    - Dream scneario could be: find companies like this company, or find orthodontic laboratories in italym 
- Find potential leads (person driven)
    - I guess this could also be a thing? ß    



- Finalize potential leads.
    The end goal of potential leads is to find a list of company name / domain name / linked in url / faceobook url / quick scan (potential lab)
    - we can use clay for this , but should be agent driven again, if the agent decides to give up or use other thing then its fine 
    - should be cross checked with hubspot to make sure we do not do double work 
    - better check if is a dental lab or not, should know which type of dental lab, provide a good description. 
    - should find company size, revenue, country address etc. 
- Find contacts of leads
    - should try to find contacts of leads, again we can use clay to find them 
    - Quick google / linked sales navigator search / .. .
- Then if we have a clean .csv, send them to hubspot .


- Regular hubspot augmentations:
    - We should be able to call augment company fucntions from within our syncs if desired so that we can enrich companies on github. 