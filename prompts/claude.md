23 April: 10.00 AM
```
I test this file, with few of my input transcripts, So we have 4 pairs of input and output file.
Now think about this, we got an varity of unstructured data, and this is how we are arranging it. Before it has some meaning and after using this script its more unstrucutred and now not able to understand whats in that.

So before doing any thing else. Lets go through: instructions_details.md, in this we I have personally mentioned what i think in the start about this project and also attached the client doc for this project.

Before "- Insights from P" its all return by me and next to this its an client doc for the project.

Lets think what we need from this script, which part would this be supporting,

# **Agent 1: Research Ingestion Agent**
 # **A. Competitor Tracking** - Done
 # **B. User Conversations** - Done
 # **C. Internal Data* - Working on it,

So i have given you the thinks which i have done my self, to build this an actually workable system which i can give to my client we need to go slow focus on structure and accuracy and build something which wont break with anything and will support all other scripts.

If any questions as me then we will decide the flow of this script and later we will start working on it
```
Attached- instructions_details.md and input files: input and output files: clean

`Ouptut`

Agent 1 has three input channels:

1. Competitor tracking — web scraping, done separately
2. User conversations — YouTube, Reddit, App reviews, done separately
3. Internal data — meeting transcripts, founder notes, product discussions — this is what we are building

`Raw transcript → Clean transcript → Signal records (Feature / Risk / Action Item...)`

`client's spec actually`

1. Title
2. 2-line summary
3. Major Decision
4. Problem
5. Possible solution pitched
6. Tone — positive / negative
7. Timeline of discussion
8. Improvement — for next call

Output Schema:

`Source Type | Entity | Signal Type | Content | Timestamp`