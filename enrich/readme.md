Goal of enrich module is to extract raw data. 
We can easily add enrichments later. 

Enrich gets as input a company name + company domain. 
It aims to find (can be modular):
- Is it a dental lab yes or no => looks on the website with a well defined prompt etc. 
- What devices do they make (scraped from the website) category between:
    - dentures
    - crowns
    - ... 
    - orthodontic expanders
    - orthodontic models
- Freeform description of what they make
- Description of the company
- Company size  
- Company socials 
- Company location/address
- Is it part of a group and which one? 
- Decision makers. 



Enrich has access to clay API which we can use to setup some things. 
I think it makes sense to push a lot of the actual lgoic to clay but just handle the questions and stuff here. 


Qualify: 
There are different purposes. 
If there is an actual acount then we need to qualify it suspicious or not. 
If users are actually using and they had account qualification status rejected/suspicious then they appear again. 






